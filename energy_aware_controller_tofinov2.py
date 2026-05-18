import socket
import math
from dataclasses import dataclass
from typing import Dict, List, Set, Protocol
import bfrt_grpc.client as gc

@dataclass(frozen=True)
class ServerStat:
    host: str
    score: float
    util: float

    @classmethod
    def parse(cls, raw_msg: str) -> 'ServerStat':
        """
        Parses raw UDP bytes into a strictly typed data structure.
        Failure to parse happens here, before the system state is ever touched.
        """
        parts = raw_msg.strip().split(",")
        if len(parts) != 3:
            raise ValueError(f"Expected format 'host,score,util', got: {raw_msg}")
        
        return cls(
            host=parts[0].strip(),
            score=float(parts[1].strip()),
            util=float(parts[2].strip())
        )

class LoadBalancingPolicy(Protocol):
    """
    Protocol defining the strict boundary for any load balancing policy.
    Let your datatypes inform your code, don’t let your code control your datatypes.
    """
    def observe(self, stat: ServerStat) -> None:
        """Update internal policy state with a new observation, if necessary."""
        ...

    def evaluate(self, stats: Dict[str, ServerStat], n_servers: int) -> List[str]:
        """Returns an ordered list of N hostnames prioritized by the policy."""
        ...

class PerformanceOnlyPolicy:
    def observe(self, stat: ServerStat) -> None:
        pass # Stateless policy

    def evaluate(self, stats: Dict[str, ServerStat], n_servers: int) -> List[str]:
        ordered = sorted(stats.values(), key=lambda s: s.util)
        return [s.host for s in ordered[:n_servers]]

class EnergyAwarePolicy:
    def observe(self, stat: ServerStat) -> None:
        pass # Stateless policy

    def evaluate(self, stats: Dict[str, ServerStat], n_servers: int) -> List[str]:
        available = [s for s in stats.values() if s.util < 70.0]
        busy = [s for s in stats.values() if s.util >= 70.0]
        
        available.sort(key=lambda s: s.score, reverse=True)
        busy.sort(key=lambda s: s.score, reverse=True)
        
        ordered = available + busy
        return [s.host for s in ordered[:n_servers]]

class MabPolicy:
    def __init__(self):
        self.mab_counts: Dict[str, int] = {}
        self.mab_explored: Set[str] = set()
        self.mab_values: Dict[str, float] = {}
        self.mab_total_pulls: int = 0

    def observe(self, stat: ServerStat) -> None:
        if stat.host not in self.mab_counts:
            self.mab_counts[stat.host] = 0
            self.mab_values[stat.host] = 0.0
       
        if stat.score > 0:
            self.mab_explored.add(stat.host)
            self.mab_values[stat.host] = stat.score

    def evaluate(self, stats: Dict[str, ServerStat], n_servers: int) -> List[str]:
        ucb_scores = []
        
        for host, stat in stats.items():
            if host not in self.mab_explored:
                ucb_scores.append((host, float('inf')))
                continue
                
            exploitation = self.mab_values[host]
            exploration = 0
            if self.mab_total_pulls > 1:
                exploration = math.sqrt((2 * math.log(self.mab_total_pulls)) / float(self.mab_counts[host]))
            
            ucb = exploitation + exploration
            if stat.util < 95.0:    
                ucb_scores.append((host, ucb))
            
        ucb_scores.sort(key=lambda x: x[1], reverse=True)
        ordered_hosts = [x[0] for x in ucb_scores[:n_servers]]

        if ordered_hosts:
            first_host = ordered_hosts[0]
            self.mab_counts[first_host] += 1
            self.mab_total_pulls += 1
        
        print(f"--- MAB Algorithm Evaluated Priority: {ordered_hosts} ---")
        return ordered_hosts

# ---------------------------------------------------------
# THE CONTROLLER
# ---------------------------------------------------------
class MyLBController:
    def __init__(self, policy: LoadBalancingPolicy, program_name="load_balance", grpc_addr="127.0.0.1:50052"):
        self.server_stats: Dict[str, ServerStat] = {}
        self.current_allocations = {}
        self.installed_keys = {}
        self.policy = policy # Dependency Injection of our Type-Safe Policy

        print("--- Initializing BFRT Connection ---")
        self.client_id = 0
        self.device_id = 0

        self.bfrt_interface = gc.ClientInterface(grpc_addr, self.client_id, self.device_id)
        self.bfrt_interface.bind_pipeline_config(program_name)
        self.bfrt_info = self.bfrt_interface.bfrt_info_get(program_name)
        self.target = gc.Target(device_id=self.device_id, pipe_id=0xFFFF)

        self.egress_table = self.bfrt_info.table_get("pipe.SwitchEgress.send_frame")
        self.nat_table = self.bfrt_info.table_get("pipe.SwitchIngress.server_src_nat")
        self.ecmp_table = self.bfrt_info.table_get("pipe.SwitchIngress.ecmp_nhop")

        print("--- Switch Programmed Successfully ---\n")

        self.install_egress_rewrite_rules()
        self.install_return_path_rule()

        print("Initializing Default Forwarding Rules (h2, h3)...")
        self.update_switch_tables(["h2", "h3"])
        self.verify_table_state()

        print("Controller is ready and listening.")
        self.run_listener()


    def ipv4_to_bytes(self, ip_str):
        """Helper to convert IPv4 strings to bytearrays for BFRT."""
        import socket
        return bytearray(socket.inet_aton(ip_str))
    
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

    def install_egress_rewrite_rules(self):
        print("Installing Egress Rewrite Rules (Source MAC Rewriting)...")
        # These are the MACs the Switch uses as its "identity" for each segment
        # Port 64: Client (p4server2)
        # Port 132: Server h2 (p4server1)
        # Port 180: Server h3 (p4server3)
        port_mac_map = {
            40:  "00:00:00:00:01:01", 
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
        client_mac = "94:6d:ae:5c:86:b2"
        
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

    def update_switch_tables(self, priority_hosts: List[str]):
        """Updated to accept a strongly typed List[str] representing prioritized hostnames."""
        server_info = {
            "h2": {"ip": "10.0.1.1", "mac": "94:6d:ae:5c:87:72", "port": 132},
            "h3": {"ip": "10.0.1.2", "mac": "94:6d:ae:5d:fd:9c", "port": 180},            
        }

        print(f"--- Logic Update: Switch Priority {priority_hosts} ---")

        for index, hostname in enumerate(priority_hosts):
            if hostname not in server_info:
                print(f"   > Warning: Unknown hostname '{hostname}' in priority list")
                continue
            
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
            
            try:
                if current == hostname:
                    continue
                elif current is not None:
                    self.ecmp_table.entry_mod(self.target, [key], [data])
                else:
                    self.ecmp_table.entry_add(self.target, [key], [data])
                    print(f"   > Index {index}: Inserted {hostname}")

                self.installed_keys[index] = hostname
            except Exception as e_insert:
                print(f"!!! CRITICAL ERROR writing Index {index} !!!")
                print(f"    INSERT/MOD Error: {e_insert}")

    def run_listener(self):
        print("Starting UDP Listener on Port 50001...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))

        while True:
            data, addr = sock.recvfrom(1024)
            try:
                stat = ServerStat.parse(data.decode())
                
                self.server_stats[stat.host] = stat
                
                self.policy.observe(stat)

                print(f"Received update from {stat.host}: Score={stat.score}, Util={stat.util}%")
                
                ordered_hosts = self.policy.evaluate(self.server_stats, n_servers=1)
                if ordered_hosts:
                    self.update_switch_tables(ordered_hosts)
                    
            except ValueError as ve:
                print(f"Parse Error (rejected invalid input before processing): {ve}")
            except Exception as e:
                print(f"Unexpected execution error: {e}")

if __name__ == "__main__":
    active_policy = MabPolicy() 
    # active_policy = EnergyAwarePolicy()
    
    ctrl = MyLBController(
        policy=active_policy, 
        program_name="load_balance", 
        grpc_addr="127.0.0.1:50052"
    )