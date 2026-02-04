// SPDX-License-Identifier: Apache-2.0
/* -*- P4_16 -*- */
#include <core.p4>
#include <v1model.p4>

/*************************************************************************
*********************** H E A D E R S  ***********************************
*************************************************************************/

header ethernet_t {
    bit<48> destinationAddress;
    bit<48> sourceAddress;
    bit<16> ethernetType;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLength;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> sourceAddress;
    bit<32> destinationAddress;
}

header tcp_t {
    bit<16> sourcePort;
    bit<16> destinationPort;
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

struct metadata {
    bit<14> ecmp_select;
}

struct headers {
    ethernet_t ethernet;
    ipv4_t     ipv4;
    tcp_t      tcp;
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
            0x800: parse_ipv4;
            default: accept;
        }
    }
    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            6: parse_tcp;
            default: accept;
        }
    }
    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }
}

/*************************************************************************
************   C H E C K S U M    V E R I F I C A T I O N   *************
*************************************************************************/

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/*************************************************************************
**************  I N G R E S S   P R O C E S S I N G   *******************
*************************************************************************/

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    register<bit<1>>(16384) connecion_filter;
    
    register<bit<32>>(1) rr_counter;

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action select_new_server(bit<32> max_servers) {
        bit<32> current_idx;
        rr_counter.read(current_idx, 0);
        meta.ecmp_select = (bit<14>)current_idx;
        
        // Update counter for next new connection
        bit<32> next_idx = (current_idx + 1) % max_servers;
        rr_counter.write(0, next_idx);
    }

    action set_nhop(bit<48> nhop_dmac, bit<32> nhop_ipv4, bit<9> port) {
        hdr.ethernet.dstAddr = nhop_dmac;
        hdr.ipv4.dstAddr = nhop_ipv4;
        standard_metadata.egress_spec = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    table ecmp_nhop {
        key = {
            meta.ecmp_select: exact;
        }
        actions = {
            set_nhop;
            drop;
        }
        size = 64; 
    }

    table connection_table {
        key = {
            hdr.ipv4.srcAddr: exact;
            hdr.ipv4.dstAddr: exact;
            hdr.ipv4.protocol: exact;
            hdr.tcp.srcPort: exact;
            hdr.tcp.dstPort: exact;
        }
        actions = {
            set_nhop;
        }
        size = 1024;
    }

    apply {
        if (hdr.ipv4.isValid() && hdr.ipv4.ttl > 0) {
            // 1. Calculate flow hash for Bloom Filter and Connection Table
            bit<32> flow_hash;
            hash(flow_hash, HashAlgorithm.crc16, (bit<32>)0,
                { hdr.ipv4.srcAddr, hdr.ipv4.dstAddr, hdr.ipv4.protocol, 
                  hdr.tcp.srcPort, hdr.tcp.dstPort }, (bit<32>)16384);

            bit<1> is_known;
            connecion_filter.read(is_known, (bit<32>)flow_hash);

            if (is_known == 1 && connection_table.apply().hit) {
                // Connection exists: forwarded via connection_table hit
            } else {
                // Assume N=4 servers as per your topology
                select_new_server(4);
                ecmp_nhop.apply();
                
                connecion_filter.write((bit<32>)flow_hash, 1);
                // Note: Real hardware would use P4Runtime to "learn" this 5-tuple 
                // into the connection_table to ensure consistency.
            }
        }
    }
}

/*************************************************************************
****************  E G R E S S   P R O C E S S I N G   *******************
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
        key = {
            standard_metadata.egress_port: exact;
        }
        actions = {
            rewrite_mac;
            drop;
        }
        size = 256;
    }
    apply {
        send_frame.apply();
    }
}

/*************************************************************************
*************   C H E C K S U M    C O M P U T A T I O N   **************
*************************************************************************/

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
     apply {
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version,
              hdr.ipv4.ihl,
              hdr.ipv4.diffserv,
              hdr.ipv4.totalLen,
              hdr.ipv4.identification,
              hdr.ipv4.flags,
              hdr.ipv4.fragOffset,
              hdr.ipv4.ttl,
              hdr.ipv4.protocol,
              hdr.ipv4.srcAddr,
              hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

/*************************************************************************
***********************  D E P A R S E R  *******************************
*************************************************************************/

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
    }
}

/*************************************************************************
***********************  S W I T C H  *******************************
*************************************************************************/

V1Switch(
MyParser(),
MyVerifyChecksum(),
MyIngress(),
MyEgress(),
MyComputeChecksum(),
MyDeparser()
) main;
