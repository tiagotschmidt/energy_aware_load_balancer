import os
import socket
import sys
import time

# This path contains the 'p4' folder and 'p4runtime_lib'
P4_UTILS_PATH = '/home/p4/tutorials/utils'
if P4_UTILS_PATH not in sys.path:
    sys.path.append(P4_UTILS_PATH)

try:
    import p4runtime_lib.bmv2 as bmv2
    import p4runtime_lib.helper as helper
    from p4.v1 import p4runtime_pb2 as p4runtime_pb2
    print("--- SUCCESS: P4 Libraries and Protobufs Loaded ---")
except ImportError as e:
    print(f"--- ERROR: Could not find P4 modules: {e} ---")
    sys.exit(1)

class RobustSwitchConnection(bmv2.Bmv2SwitchConnection):
    """
    Inherits from Bmv2SwitchConnection but overrides the broken
    MasterArbitrationUpdate method to accept custom election IDs.
    """
    def MasterArbitrationUpdate(self, dry_run=False, election_id_low=1, **kwargs):
        request = p4runtime_pb2.StreamMessageRequest()
        request.arbitration.device_id = self.device_id
        request.arbitration.election_id.high = 0
        
        request.arbitration.election_id.low = election_id_low 

        if dry_run:
            print("P4Runtime MasterArbitrationUpdate: ", request)
        else:
            self.requests_stream.put(request)
            for item in self.stream_msg_resp:
                return item 

class MyLBController:
    def __init__(self, p4info_path, bmv2_json_path):
        self.p4info_helper = helper.P4InfoHelper(p4info_path)
        self.server_stats = {} 
        
        connected = False
        for i in range(5):
            try:
                self.sw = RobustSwitchConnection(
                    name='s1',
                    address='127.0.0.1:50051',
                    device_id=0)
                
                self.sw.MasterArbitrationUpdate(election_id_low=100)
                
                connected = True
                print("Connected to switch as Master (ID=100)!")
                break
            except Exception as e:
                print(f"Switch not ready, retrying in 2 seconds... ({i+1}/5) Error: {e}")
                time.sleep(2)
            
        if not connected:
            print("Failed to connect to switch after 5 attempts.")
            sys.exit(1)
        
        print("Controller is ready and listening.")

    def run_listener(self):
        """UDP Listener for periodic messaging from server agents."""
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
                print(f"Error parsing message '{data}': {e}")

    def recompute_and_update(self, N=2):
        """Implements the Tiered Selection Policy."""
        available_set = []
        busy_set = []

        for host, (score, util) in self.server_stats.items():
            if util < 90.0:
                available_set.append((host, score))
            else:
                busy_set.append((host, score))

        available_set.sort(key=lambda x: x[1], reverse=True)
        busy_set.sort(key=lambda x: x[1], reverse=True)

        ordered_servers = (available_set + busy_set)[:N]
        if ordered_servers:
            self.update_switch_tables(ordered_servers)

    def update_switch_tables(self, priority_list):
        server_info = {
            "h2": {"ip": "10.0.2.2", "mac": "08:00:00:00:02:02", "port": 2},
            "h3": {"ip": "10.0.3.3", "mac": "08:00:00:00:03:03", "port": 3},
        }

        print(f"--- Logic Update: New Priority {priority_list} ---")

        for index, server_tuple in enumerate(priority_list):
            hostname = server_tuple[0]
            score = server_tuple[1]
            
            if hostname not in server_info: continue
            info = server_info[hostname]

            table_entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.ecmp_nhop",
                match_fields={"meta.ecmp_select": index},
                action_name="MyIngress.set_nhop",
                action_params={
                    "nhop_dmac": info["mac"],
                    "nhop_ipv4": info["ip"],
                    "port": info["port"],
                },
            )

            try:
                self.sw.WriteTableEntry(table_entry)
                print(f"   > Index {index}: Inserted {hostname}")
            except Exception:
                try:
                    self.sw.WriteTableEntry(table_entry, p4runtime_pb2.Update.MODIFY)
                    print(f"   > Index {index}: Updated to {hostname}")
                except Exception as e:
                    print(f"   > Index {index}: Error: {e}")

if __name__ == "__main__":
    ctrl = MyLBController("build/load_balance.p4info.txtpb", "build/load_balance.json")
    ctrl.run_listener()