/*******************************************************************************
 *  INTEL CONFIDENTIAL
 *
 *  Copyright (c) 2021 Intel Corporation
 *  All Rights Reserved.
 *
 *  This software and the related documents are Intel copyrighted materials,
 *  and your use of them is governed by the express license under which they
 *  were provided to you ("License"). Unless the License provides otherwise,
 *  you may not use, modify, copy, publish, distribute, disclose or transmit
 *  this software or the related documents without Intel's prior written
 *  permission.
 *
 *  This software and the related documents are provided as is, with no express
 *  or implied warranties, other than those that are expressly stated in the
 *  License.
 ******************************************************************************/


#include <core.p4>
#if __TARGET_TOFINO__ == 3
#include <t3na.p4>
#elif __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

const bit<32> VIP_ADDRESS = 0x0A000001; // 10.0.0.1

#define NUM_PORTS 512
#define REFRESH_INTERVAL_MS 3000
#define REFRESH_INTERVAL (REFRESH_INTERVAL_MS * 1000000 / (1 << 16))

#include "headers.p4"
#include "util.p4"


struct metadata_t {
    bit<16> ecmp_select;
}


// ---------------------------------------------------------------------------
// Ingress parser
// ---------------------------------------------------------------------------
parser SwitchIngressParser(
        packet_in pkt,
        out header_t hdr,
        out metadata_t ig_md,
        out ingress_intrinsic_metadata_t ig_intr_md) {
    TofinoIngressParser() tofino_parser;
    Checksum() ipv4_checksum;
    ParserCounter() counter;

    state start {
        tofino_parser.apply(pkt, ig_intr_md);
        transition parse_ethernet;
    }

    state parse_ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            ETHERTYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        ipv4_checksum.add(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            // IP_PROTOCOLS_TCP: parse_tcp;
            IP_PROTOCOLS_UDP: parse_udp;
            default: accept;
        }
    }

    // state parse_tcp {
    //     pkt.extract(hdr.tcp);
    //     transition accept;
    // }
    
    state parse_udp{
        pkt.extract(hdr.udp);
        transition accept;
    }
}

// ---------------------------------------------------------------------------
// Ingress Deparser
// ---------------------------------------------------------------------------
control SwitchIngressDeparser(
        packet_out pkt,
        inout header_t hdr,
        in metadata_t ig_md,
        in ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md) {
    Checksum() ipv4_checksum;
    Mirror() mirror;
    apply {
        if(hdr.ipv4.isValid()){
            hdr.ipv4.hdrChecksum = ipv4_checksum.update({
                hdr.ipv4.version,
                hdr.ipv4.ihl,
                hdr.ipv4.diffserv,
                hdr.ipv4.total_len,
                hdr.ipv4.identification,
                hdr.ipv4.flags,
                hdr.ipv4.frag_offset,
                hdr.ipv4.ttl,
                hdr.ipv4.protocol,
                hdr.ipv4.srcAddr,
                hdr.ipv4.dstAddr
            });
        }
        pkt.emit(hdr.ethernet);
        pkt.emit(hdr.ipv4);
        pkt.emit(hdr.udp);
    }
}

control SwitchIngress(
        inout header_t hdr,
        inout metadata_t meta,
        in ingress_intrinsic_metadata_t ig_intr_md,
        in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
        inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
        inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {
    const bit<32> POOL_SIZE = 2;
    Register<bit<32>, bit<32>>(1) rr_register;  
    RegisterAction<bit<32>, bit<32>, bit<32>>(rr_register) rr_action = {
    void apply(inout bit<32> state_val, out bit<32> return_val) {
        return_val = state_val;

        // // 2. Increment and wrap around (simulating the % operator)
        // if (state_val == POOL_SIZE - 1) {
        //     state_val = 0;
        // } else {
        //     state_val = state_val + 1;
        // }
        state_val = 0;
    }
    };
    
    bit<32> current_rr_val;

    action drop() {
        ig_dprsr_md.drop_ctl = 0;
    }

    // --- FORWARD PATH ACTION (Client -> Server) ---
    // Combines DNAT + Routing + MAC Rewrite in one step
    action forward_to_server(bit<48> server_mac, bit<32> server_ip, bit<16> port) {
        hdr.ethernet.dstAddr = server_mac;
        hdr.ipv4.dstAddr = server_ip;
        // standard_metadata.egress_spec = (bit<9>)port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
        ig_tm_md.ucast_egress_port = (bit<9>)port; 
    }

    action select_new_server(bit<32> max_servers) {
        bit<32> current_idx;
        current_rr_val = rr_action.execute(0);     
        current_idx = current_rr_val;
        meta.ecmp_select = (bit<16>)current_idx;
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
        // standard_metadata.egress_spec = (bit<9>)port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
        ig_tm_md.ucast_egress_port = port;
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
                 select_new_server(2); 
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

parser SwitchEgressParser(
    packet_in pkt,
    out header_t hdr,
    out metadata_t eg_md,
    out egress_intrinsic_metadata_t eg_intr_md) {

    TofinoEgressParser() tofino_parser;
    Checksum() ipv4_checksum;
    ParserCounter() counter;

    state start {
        tofino_parser.apply(pkt, eg_intr_md);
        transition parse_ethernet;
    }

    state parse_ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            ETHERTYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        ipv4_checksum.add(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTOCOLS_UDP: parse_udp;
            default: accept;
        }
    }

    state parse_udp {
        pkt.extract(hdr.udp);
        transition accept;
    }
}


control SwitchEgress(
        inout header_t hdr,
        inout metadata_t eg_md,
        in egress_intrinsic_metadata_t eg_intr_md,
        in egress_intrinsic_metadata_from_parser_t eg_intr_md_from_prsr,
        inout egress_intrinsic_metadata_for_deparser_t ig_intr_dprs_md,
        inout egress_intrinsic_metadata_for_output_port_t eg_intr_oport_md) {
    action rewrite_mac(bit<48> smac) {
        hdr.ethernet.srcAddr = smac;
    }
    action drop() {
        ig_intr_dprs_md.drop_ctl = 0;
    }
    table send_frame {
        key = {  eg_intr_md.egress_port : exact; }
        actions = { rewrite_mac; drop; }
        size = 256;
    }
    apply {
        send_frame.apply();
    }
}

control SwitchEgressDeparser(
        packet_out pkt,
        inout header_t hdr,
        in metadata_t eg_md,
        in egress_intrinsic_metadata_for_deparser_t ig_intr_dprs_md) {

    apply {
        pkt.emit(hdr.ethernet);
        pkt.emit(hdr.ipv4);
        pkt.emit(hdr.udp);
    }
}

Pipeline(SwitchIngressParser(),
         SwitchIngress(),
         SwitchIngressDeparser(),
         SwitchEgressParser(),
         SwitchEgress(),
         SwitchEgressDeparser()) pipe;

Switch(pipe) main;

