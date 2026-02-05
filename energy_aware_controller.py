import os
import socket
import sys
import time

# This path contains the 'p4' folder and 'p4runtime_lib'
P4_UTILS_PATH = '/home/p4/tutorials/utils'
if P4_UTILS_PATH not in sys.path:
    sys.path.append(P4_UTILS_PATH)

# 2. NOW DO THE IMPORTS
try:
    import p4runtime_lib.bmv2 as bmv2
    import p4runtime_lib.helper as helper
    from p4.v1 import p4runtime_pb2 as p4runtime_pb2  # This will work now!
    print("--- SUCCESS: P4 Libraries and Protobufs Loaded ---")
except ImportError as e:
    print(f"--- ERROR: Could not find P4 modules: {e} ---")
    sys.exit(1)
    
    
class MyLBController:
    def __init__(self, p4info_path, bmv2_json_path):
        # Initialize P4Runtime helper and connection
        self.p4info_helper = helper.P4InfoHelper(p4info_path)
        self.server_stats = {}  # {hostname: (score, util)}
        # Add a retry loop to wait for the switch to wake up
        connected = False
        for i in range(5):
            try:
                self.sw = bmv2.Bmv2SwitchConnection(
                    name='s1',
                    address='127.0.0.1:50051',
                    device_id=0)
                self.sw.MasterArbitrationUpdate()
                connected = True
                print("Connected to switch!")
                break
            except Exception as e:
                print(f"Switch not ready, retrying in 2 seconds... ({i+1}/5)")
                time.sleep(2)
            
        if not connected:
            print("Failed to connect to switch after 5 attempts.")
            sys.exit(1)
        
        print("Installing P4 pipeline config...")
        self.sw.SetForwardingPipelineConfig(p4info=self.p4info_helper.p4info,
                                            bmv2_json_file_path=bmv2_json_path)
        print("Pipeline config installed successfully!")

    def run_listener(self):
        """UDP Listener for periodic messaging from server agents."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))
        while True:
            data, _ = sock.recvfrom(1024)
            # Parse format: "hostname,score,util"
            host, score, util = data.decode().split(",")
            self.server_stats[host] = (float(score), float(util))
            self.recompute_and_update()

    def recompute_and_update(self, N=2):
        """Implements the Tiered Selection Policy (Algorithm 1)."""
        available_set = []
        busy_set = []

        for host, (score, util) in self.server_stats.items():
            if util < 90.0:
                available_set.append((host, score))
            else:
                busy_set.append((host, score))

        # Sort by efficiency score descending
        available_set.sort(key=lambda x: x[1], reverse=True)
        busy_set.sort(key=lambda x: x[1], reverse=True)

        # Merge sets: Available first, then Busy
        ordered_servers = (available_set + busy_set)[:N]
        self.update_switch_tables(ordered_servers)

    def update_switch_tables(self, priority_list):
        """Installs the updated priority ordering into the ecmp_nhop table."""

        # Static mapping based on your topology.json
        server_info = {
            "p4dev": {"ip": "10.0.2.2", "mac": "08:00:00:00:02:02", "port": 2},
            "h2":    {"ip": "10.0.2.2", "mac": "08:00:00:00:02:02", "port": 2},
            "h3":    {"ip": "10.0.3.3", "mac": "08:00:00:00:03:03", "port": 3},
            "h4":    {"ip": "10.0.4.4", "mac": "08:00:00:00:04:04", "port": 4},
            "h5":    {"ip": "10.0.5.5", "mac": "08:00:00:00:05:05", "port": 5},
        }

        print(f"\n--- Updating Switch Rules ---")
        print(f"Current Priority: {priority_list}")

        # priority_list is like [('h2', 85.5), ('h3', 40.2)]
        for index, server_tuple in enumerate(priority_list):
            hostname = server_tuple[0]
            score = server_tuple[1]
            
            if hostname not in server_info:
                print(f"SKIPPING: Unknown host '{hostname}'")
                continue

            info = server_info[hostname]

            # Build the Table Entry for the 'ecmp_nhop' table
            table_entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.ecmp_nhop",
                match_fields={
                    "meta.ecmp_select": index  # Match the index
                },
                action_name="MyIngress.set_nhop",
                action_params={
                    "nhop_dmac": info["mac"],
                    "nhop_ipv4": info["ip"],
                    "port": info["port"],
                },
            )

            try:
                # 1. Try to INSERT the rule (works if index is empty)
                self.sw.WriteTableEntry(table_entry)
                print(f"SUCCESS: Inserted {hostname} at Index {index} (Score: {score:.2f})")
            except Exception:
                try:
                    # 2. If index is occupied, MODIFY the existing rule
                    # Note: tutorial library uses update_type as a second positional argument
                    self.sw.WriteTableEntry(table_entry, p4runtime_pb2.Update.MODIFY)
                    print(f"SUCCESS: Updated Index {index} to {hostname} (Score: {score:.2f})")
                except Exception as e:
                    print(f"CRITICAL ERROR at Index {index}: {e}")

if __name__ == "__main__":
    ctrl = MyLBController("build/load_balance.p4info.txtpb", "build/load_balance.json")
    ctrl.run_listener()
