/* -*- P4_16 -*- */
#include <core.p4>
#include <v1model.p4>

const bit<32> VIP_ADDRESS = 0x0A000001; // 10.0.0.1

/*************************************************************************
*********************** H E A D E R S  ***********************************
*************************************************************************/

header ethernet_t {
    bit<48> dstAddr;
    bit<48> srcAddr;
    bit<16> etherType;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLen;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> srcAddr;
    bit<32> dstAddr;
}

header tcp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<32> seqNo;
    bit<32> ackNo;
    bit<4>  dataOffset;
    bit<3>  res;
    bit<3>  ecn;
    bit<6>  ctrl;
    bit<16> window;
    bit<16> checksum;
    bit<16> urgentPtr;
}

header udp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<16> length;
    bit<16> checksum;
}

header icmp_t {
    bit<8> type;
    bit<8> code;
    bit<16> checksum;
    bit<16> identifier;
    bit<16> sequence_number;
}

header arp_t {
    bit<16> htype;
    bit<16> ptype;
    bit<8>  hlen;
    bit<8>  plen;
    bit<16> opcode;
    bit<48> senderMacAddr;
    bit<32> senderIpAddr;
    bit<48> targetMacAddr;
    bit<32> targetIpAddr;
}

struct metadata {
    bit<16> ecmp_select;
}

struct headers {
    ethernet_t ethernet;
    arp_t      arp;
    ipv4_t     ipv4;
    tcp_t      tcp;
    udp_t      udp;
    icmp_t     icmp;
}

/*************************************************************************
*********************** P A R S E R  ***********************************
*************************************************************************/

parser MyParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            0x0800: parse_ipv4;
            0x0806: parse_arp;
            default: accept;
        }
    }

    state parse_arp {
        packet.extract(hdr.arp);
        transition accept;
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            6: parse_tcp;
            17: parse_udp;
            1: parse_icmp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition accept;
    }

    state parse_icmp {
        packet.extract(hdr.icmp);
        transition accept;
    }
}

/*************************************************************************
************ C H E C K S U M    V E R I F I C A T I O N   *************
*************************************************************************/

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply {
        verify_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.totalLen, hdr.ipv4.identification, hdr.ipv4.flags,
              hdr.ipv4.fragOffset, hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

/*************************************************************************
************** I N G R E S S   P R O C E S S I N G   *******************
*************************************************************************/

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    // --- CONNECTION CONSISTENCY REGISTERS ---
    // register<bit<1>>(16384) flow_bloom_filter; 
    // register<bit<14>>(16384) flow_server_map;
    register<bit<32>>(1) rr_counter;

    action drop() {
        mark_to_drop(standard_metadata);
    }

    // --- FORWARD PATH ACTION (Client -> Server) ---
    // Combines DNAT + Routing + MAC Rewrite in one step
    action forward_to_server(bit<48> server_mac, bit<32> server_ip, bit<16> port) {
        hdr.ethernet.dstAddr = server_mac;
        hdr.ipv4.dstAddr = server_ip;
        standard_metadata.egress_spec = (bit<9>)port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    action select_new_server(bit<32> max_servers) {
        bit<32> current_idx;
        rr_counter.read(current_idx, 0);
        meta.ecmp_select = (bit<16>)current_idx;
        bit<32> next_idx = (current_idx + 1) % max_servers;
        rr_counter.write(0, next_idx);
    }

    table ecmp_nhop {
        key = { meta.ecmp_select: exact; }
        actions = { forward_to_server; drop; }
        size = 64; 
    }

    // --- REVERSE PATH ACTION (Server -> Client) ---
    // Combines SNAT + Routing + MAC Rewrite in one step
    action nat_reply_to_client(bit<48> client_mac, bit<9> port) {
        hdr.ipv4.srcAddr = VIP_ADDRESS; // Hide Server IP (SNAT)
        hdr.ethernet.dstAddr = client_mac;
        standard_metadata.egress_spec = (bit<9>)port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    table server_src_nat {
        key = {
            // FIXED TABLE STRATEGY: 
            hdr.ipv4.srcAddr: exact; // Source is Server (e.g. 10.0.2.2)
            hdr.ipv4.dstAddr: exact; // Dest is Client (e.g. 10.0.1.1)
        }
        actions = {
            nat_reply_to_client;
            drop;
        }
        size = 1024;
    }

    apply {
        if (hdr.ipv4.isValid()) {
            
            // Disable UDP Checksum since we are NATing
            if (hdr.udp.isValid()) {
                hdr.udp.checksum = 16w0;
            }

            // PATH 1: Client -> VIP (Load Balancer)
            if (hdr.ipv4.dstAddr == VIP_ADDRESS && hdr.ipv4.ttl > 0) {
                 
                 bit<32> hash_index;
                 // 5-tuple Hash using 0 for missing headers
                 hash(hash_index, HashAlgorithm.crc16, (bit<32>)0, {
                    hdr.ipv4.srcAddr,
                    hdr.ipv4.dstAddr,
                    hdr.ipv4.protocol,
                    hdr.tcp.srcPort, 
                    hdr.tcp.dstPort,
                    hdr.udp.srcPort, 
                    hdr.udp.dstPort
                 }, (bit<32>)16384);

                //  bit<1> is_known_flow;
                //  flow_bloom_filter.read(is_known_flow, hash_index);

                //  if (is_known_flow == 1) {
                //      // Sticky Session
                //      flow_server_map.read(meta.ecmp_select, hash_index);
                //  } else {
                //     //  New Session
                 select_new_server(2); 
                //      flow_bloom_filter.write(hash_index, 1);
                //      flow_server_map.write(hash_index, meta.ecmp_select);
                // //  }
                 ecmp_nhop.apply();
            }

            // PATH 2: Server -> Client (Reverse NAT)
            // Matches explicit Source(Server) and Destination(Client)
            else {
                server_src_nat.apply();
            }
        }
    }
}

/*************************************************************************
**************** E G R E S S   P R O C E S S I N G   *******************
*************************************************************************/

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {

    action rewrite_mac(bit<48> smac) {
        hdr.ethernet.srcAddr = smac;
    }
    action drop() {
        mark_to_drop(standard_metadata);
    }
    table send_frame {
        key = { standard_metadata.egress_port: exact; }
        actions = { rewrite_mac; drop; }
        size = 256;
    }
    apply {
        send_frame.apply();
    }
}

/*************************************************************************
************* C H E C K S U M    C O M P U T A T I O N   **************
*************************************************************************/

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
     apply {
        // Only IPv4 Checksum is strictly required
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.totalLen, hdr.ipv4.identification, hdr.ipv4.flags,
              hdr.ipv4.fragOffset, hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

/*************************************************************************
*********************** D E P A R S E R  *******************************
*************************************************************************/

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.arp);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
        packet.emit(hdr.udp);
        packet.emit(hdr.icmp);
    }
}

V1Switch(
MyParser(),
MyVerifyChecksum(),
MyIngress(),
MyEgress(),
MyComputeChecksum(),
MyDeparser()
) main;