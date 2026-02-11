import socket
import sys


# This path contains the 'p4' folder and 'p4runtime_lib'
P4_UTILS_PATH = '/home/p4/tutorials/utils'
if P4_UTILS_PATH not in sys.path:
    sys.path.append(P4_UTILS_PATH)

try:
    import p4runtime_lib
    import p4runtime_lib.bmv2 as bmv2
    import p4runtime_lib.helper as helper
    from p4.v1 import p4runtime_pb2 as p4runtime_pb2
    print("--- SUCCESS: P4 Libraries and Protobufs Loaded ---")
except ImportError as e:
    print(f"--- ERROR: Could not find P4 modules: {e} ---")
    sys.exit(1)


BUILD_DIR = "build"
JSON_FILE = f"{BUILD_DIR}/load_balance.json"
P4INFO_FILE = f"{BUILD_DIR}/load_balance.p4.p4info.txtpb"
GRPC_PORT = 50051 

class MyLBController:
    def __init__(self, p4info_path, bmv2_json_path):
        self.p4info_helper = helper.P4InfoHelper(p4info_path)
        self.bmv2_json_path = bmv2_json_path
        self.server_stats = {} 

        self.s1_conn = p4runtime_lib.bmv2.Bmv2SwitchConnection(
            name='s1',
            address=f'127.0.0.1:{50051}',
            device_id=0
        )
        self.s1_conn.MasterArbitrationUpdate()
        
        # Set Pipeline (The "Push")
        self.s1_conn.SetForwardingPipelineConfig(
            p4info=self.p4info_helper.p4info,
            bmv2_json_file_path=JSON_FILE
        )
        print("--- Switch Programmed Successfully ---\n")

        # 2. Install Static Rules
        self.install_egress_rewrite_rules()
        self.install_return_path_rule()
        
        # 3. Install Default Forwarding Rules
        print("Initializing Default Forwarding Rules (h2, h3)...")
        default_servers = [("h2", 0), ("h3", 0)] 
        self.update_switch_tables(default_servers)

        # 4. Verify
        self.verify_table_state()

        print("Controller is ready and listening.")

    def set_forwarding_pipeline(self):
        print("--- Setting Forwarding Pipeline Config ---")
        try:
            self.s1_conn.SetForwardingPipelineConfig(
                p4info=self.p4info_helper.p4info,
                bmv2_json_file_path=self.bmv2_json_path
            )
            print("   > Pipeline Configured Successfully.")
        except Exception as e:
            print(f"   > Error setting pipeline: {e}")
            # We don't exit here because sometimes it succeeds despite errors if identical

    def install_egress_rewrite_rules(self):
        print("Installing Egress Rewrite Rules...")
        port_mac_map = {
            1: "00:00:00:00:01:01",
            2: "00:00:00:00:02:02",
            3: "00:00:00:00:03:03"
        }
        for port, smac in port_mac_map.items():
            entry = self.p4info_helper.buildTableEntry(
                table_name="MyEgress.send_frame",
                match_fields={"standard_metadata.egress_port": port},
                action_name="MyEgress.rewrite_mac",
                action_params={"smac": smac}
            )
            try:
                self.s1_conn.WriteTableEntry(entry)
                print(f"   > Egress Rule: Port {port} -> SMAC {smac}")
            except Exception as e:
                if "ALREADY_EXISTS" not in str(e):
                    print(f"   > Error installing egress rule: {e}")

    def install_return_path_rule(self):
        print("Installing Fixed Return Path Rules (Server IP -> Client IP)...")
        client_ip = "10.0.1.1"
        client_port = 1
        client_mac = "08:00:00:00:01:01"
        servers = ["10.0.2.2", "10.0.3.3"]

        for server_ip in servers:
            entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.server_src_nat",
                match_fields={
                    "hdr.ipv4.srcAddr": server_ip,
                    "hdr.ipv4.dstAddr": client_ip
                },
                action_name="MyIngress.nat_reply_to_client",
                action_params={
                    "client_mac": client_mac, 
                    "port": client_port
                }
            )
            try:
                self.s1_conn.WriteTableEntry(entry)
                print(f"   > Return Rule: Src {server_ip} -> Dst {client_ip}")
            except Exception as e:
                 if "ALREADY_EXISTS" not in str(e):
                    print(f"   > Error installing return rule: {e}")

    def update_switch_tables(self, priority_list):
        server_info = {
            "h2": {"ip": "10.0.2.2", "mac": "08:00:00:00:02:02", "port": 2},
            "h3": {"ip": "10.0.3.3", "mac": "08:00:00:00:03:03", "port": 3},
        }

        print(f"--- Logic Update: New Priority {priority_list} ---")

        for index, server_tuple in enumerate(priority_list):
            hostname = server_tuple[0]
            if hostname not in server_info: continue
            info = server_info[hostname]

            table_entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.ecmp_nhop",
                match_fields={"meta.ecmp_select": index},
                action_name="MyIngress.forward_to_server",
                action_params={
                    "server_mac": info["mac"],
                    "server_ip": info["ip"],
                    "port": info["port"],
                },
            )

            # --- ROBUST WRITE LOGIC ---
            try:
                # 1. Try to INSERT (Optimistic)
                self.s1_conn.WriteTableEntry(table_entry)
                print(f"   > Index {index}: Inserted {hostname}")
            except Exception as e_insert:
                # 2. If INSERT fails (for ANY reason), try MODIFY
                # We don't check string content because BMv2 returns erratic codes.
                try:
                    self.s1_conn.WriteTableEntry(table_entry, p4runtime_pb2.Update.MODIFY)
                    print(f"   > Index {index}: Updated to {hostname}")
                except Exception as e_modify:
                    # 3. If BOTH fail, then we have a real problem
                    print(f"!!! CRITICAL ERROR writing Index {index} !!!")
                    print(f"    INSERT Error: {e_insert}")
                    print(f"    MODIFY Error: {e_modify}")

    def verify_table_state(self):
        print("\n--- VERIFYING SWITCH STATE ---")
        tables = ["MyIngress.ecmp_nhop", "MyIngress.server_src_nat"]
        for table_name in tables:
            try:
                table_id = self.p4info_helper.get_tables_id(table_name)
                count = 0
                for response in self.s1_conn.ReadTableEntries(table_id=table_id, dry_run=False):
                    for _ in response.entities: count += 1
                
                if count == 0:
                    print(f"  [WARNING] Table {table_name} is EMPTY! (Write Failed)")
                else:
                    print(f"  [OK] Table {table_name} has {count} entries.")
            except Exception as e:
                print(f"  [ERROR] Failed to read {table_name}: {e}")
        print("------------------------------\n")

    def run_listener(self):
        print("Starting UDP Listener on Port 50001...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))
        
        while True:
            data, addr = sock.recvfrom(1024)
            try:
                msg = data.decode().strip()
                host, score, util = msg.split(",")
                self.server_stats[host] = (float(score), float(util))
                print(f"Received update from {host}: Score={score}, Util={util}%")
                self.recompute_and_update()
            except Exception as e:
                print(f"Error parsing message: {e}")

    def recompute_and_update(self, N=1):
        ordered = self.performance_only_priority(N)
        # ordered = self.energy_aware_priority(N)
        if ordered: self.update_switch_tables(ordered)

    def energy_aware_priority(self, N):
        available = []
        busy = []
        for host, (score, util) in self.server_stats.items():
            if util < 90.0: available.append((host, score))
            else: busy.append((host, score))
        available.sort(key=lambda x: x[1], reverse=True)
        busy.sort(key=lambda x: x[1], reverse=True)
        ordered = (available + busy)[:N]
        if ordered: self.update_switch_tables(ordered)
    
    def performance_only_priority(self, N):
        allServers = []
        for host, (score, util) in self.server_stats.items():
            allServers.append((host, util))
        allServers.sort(key=lambda x: x[1], reverse=False)
        ordered = (allServers)[:N]
        return ordered

if __name__ == "__main__":
    ctrl = MyLBController("build/load_balance.p4.p4info.txtpb", "build/load_balance.json")
    ctrl.run_listener()