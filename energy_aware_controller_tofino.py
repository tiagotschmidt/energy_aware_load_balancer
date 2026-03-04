import socket
import bfrt_grpc.client as gc


class MyLBController:
    def __init__(self, program_name="load_balance", grpc_addr="127.0.0.1:50052"):
        self.server_stats = {}
        self.current_allocations = {}
        self.installed_keys = {}

        print("--- Initializing BFRT Connection ---")
        self.client_id = 0
        self.device_id = 0

        # 1. Connect and Bind to Tofino
        self.bfrt_interface = gc.ClientInterface(
            grpc_addr, self.client_id, self.device_id
        )
        self.bfrt_interface.bind_pipeline_config(program_name)
        self.bfrt_info = self.bfrt_interface.bfrt_info_get(program_name)
        self.target = gc.Target(device_id=self.device_id, pipe_id=0xFFFF)

        # Retrieve table objects (Note: Tofino prepends 'pipe.' to block names)
        self.egress_table = self.bfrt_info.table_get("pipe.SwitchEgress.send_frame")
        self.nat_table = self.bfrt_info.table_get("pipe.SwitchIngress.server_src_nat")
        self.ecmp_table = self.bfrt_info.table_get("pipe.SwitchIngress.ecmp_nhop")

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
        self.run_listener()

    def mac_to_bytes(self, mac_str):
        """Helper to convert MAC strings to bytearrays for BFRT."""
        return bytearray.fromhex(mac_str.replace(":", ""))

    def install_egress_rewrite_rules(self):
        print("Installing Egress Rewrite Rules (Source MAC Rewriting)...")
        # These are the MACs the Switch uses as its "identity" for each segment
        # Port 64: Client (p4server2)
        # Port 132: Server h2 (p4server1)
        # Port 180: Server h3 (p4server3)
        port_mac_map = {
            64:  "00:00:00:00:01:01", 
            132: "00:00:00:00:02:02", 
            180: "00:00:00:00:03:03", 
        }
        for port, smac in port_mac_map.items():
            key = self.egress_table.make_key(
                [gc.KeyTuple("eg_intr_md.egress_port", port)]
            )
            data = self.egress_table.make_data(
                [gc.DataTuple("smac", self.mac_to_bytes(smac))],
                "SwitchEgress.rewrite_mac",
            )

            try:
                # Use entry_add; if it fails, the verify step will catch it
                self.egress_table.entry_add(self.target, [key], [data])
                print(f"   > Egress Rule: Port {port} -> SMAC {smac}")
            except Exception as e:
                if "ALREADY_EXISTS" in str(e):
                    self.egress_table.entry_mod(self.target, [key], [data])
                else:
                    print(f"   > Error on Port {port}: {e}")

    def install_return_path_rule(self):
        print("Installing Fixed Return Path Rules (Server -> Client)...")
        client_ip = "10.0.3.3"
        client_port = 40  # Based on your UP port 33/0
        client_mac = "94:6d:ae:5c:87:12"
        
        # Real Backend Server IPs from p4server1 and p4server3
        servers = ["10.0.1.2", "10.0.1.1"]

        for server_ip in servers:
            key = self.nat_table.make_key([
                gc.KeyTuple("hdr.ipv4.srcAddr", self.ipv4_to_bytes(server_ip)),
                gc.KeyTuple("hdr.ipv4.dstAddr", self.ipv4_to_bytes(client_ip)),
            ])
            data = self.nat_table.make_data([
                gc.DataTuple("client_mac", self.mac_to_bytes(client_mac)),
                gc.DataTuple("port", client_port),
            ], "SwitchIngress.nat_reply_to_client")

            try:
                self.nat_table.entry_add(self.target, [key], [data])
                print(f"   > Return Rule: Src {server_ip} -> Dst {client_ip}")
            except Exception as e:
                if "ALREADY_EXISTS" not in str(e):
                    print(f"   > Error: {e}")

    def update_switch_tables(self, priority_list):
        # PHYSICAL TOPOLOGY MAPPING
        server_info = {
            "h2": {
                "ip": "10.0.1.1",
                "mac": "94:6d:ae:5c:87:72",
                "port": 132, # 100G Port
            },
            "h3": {
                "ip": "10.0.1.2",
                "mac": "94:6d:ae:5c:86:b2",
                "port": 180  # 10G Port
            },            
        }

        print(f"--- Logic Update: New Priority {[x[0] for x in priority_list]} ---")

        for index, server_tuple in enumerate(priority_list):
            hostname = server_tuple[0]
            if hostname not in server_info:
                print(f"   > Warning: Unknown hostname '{hostname}' in priority list")
            info = server_info[hostname]

            key = self.ecmp_table.make_key([gc.KeyTuple("meta.ecmp_select", index)])
            data = self.ecmp_table.make_data(
                [
                    gc.DataTuple("server_mac", self.mac_to_bytes(info["mac"])),
                    gc.DataTuple("server_ip", self.ipv4_to_bytes(info["ip"])),
                    gc.DataTuple("port", info["port"]),
                ],
                "SwitchIngress.forward_to_server",
            )

            current = self.installed_keys.get(index)
            print("Current is:" + str(current))

            try:
                if current == hostname:
                    print("Equal!")
                    continue
                elif current is not None:
                    print("Change (Modify)")
                    self.ecmp_table.entry_mod(self.target, [key], [data])
                else:
                    print("Write (Add)")
                    self.ecmp_table.entry_add(self.target, [key], [data])
                    print(f"   > Index {index}: Inserted {hostname}")

                self.installed_keys[index] = hostname
            except Exception as e_insert:
                print(f"!!! CRITICAL ERROR writing Index {index} !!!")
                print(f"    INSERT/MOD Error: {e_insert}")

    def verify_table_state(self):
        print("\n--- VERIFYING SWITCH STATE ---")
        tables_to_check = [
            ("MyIngress.ecmp_nhop", self.ecmp_table),
            ("MyIngress.server_src_nat", self.nat_table),
        ]

        for name, table in tables_to_check:
            try:
                # entry_get without keys returns all entries
                count = sum(1 for _ in table.entry_get(self.target))
                if count == 0:
                    print(f"  [WARNING] Table {name} is EMPTY! (Write Failed)")
                else:
                    print(f"  [OK] Table {name} has {count} entries.")
            except Exception as e:
                print(f"  [ERROR] Failed to read {name}: {e}")
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
        # ordered = self.performance_only_priority(N)
        ordered = self.energy_aware_priority(N)
        if ordered:
            self.update_switch_tables(ordered)

    def energy_aware_priority(self, N):
        available = []
        busy = []
        print(
            f"--- Logic Update: New Priority {[x[0] for x in self.server_stats.items()]} ---"
        )
        for host, (score, util) in self.server_stats.items():
            if util < 70.0:
                available.append((host, score))
            else:
                busy.append((host, score))
        available.sort(key=lambda x: x[1], reverse=False)
        busy.sort(key=lambda x: x[1], reverse=False)
        ordered = (available + busy)[:N]
        print(f"--- Logic Update: New Priority {[x[0] for x in ordered]} ---")
        return ordered

    def performance_only_priority(self, N):
        allServers = []
        for host, (score, util) in self.server_stats.items():
            allServers.append((host, util))
        allServers.sort(key=lambda x: x[1], reverse=False)
        ordered = (allServers)[:N]
        return ordered

    def ipv4_to_bytes(self, ip_str):
        """Helper to convert IPv4 strings to bytearrays for BFRT."""
        import socket

        return bytearray(socket.inet_aton(ip_str))


if __name__ == "__main__":
    # Ensure your Tofino compile outputs a program named "load_balance"
    ctrl = MyLBController(program_name="load_balance", grpc_addr="127.0.0.1:50052")
