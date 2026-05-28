import socket
import math
import logging
import bfrt_grpc.client as gc
import datetime

log_filename = f"lb_controller_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,  # Restrict to INFO to hide periodic debug data
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    filename=log_filename,
    filemode="w",
)
logger = logging.getLogger("LB_Ctrl")
    
PERFORMANCE_POLICY = True  # Set to False to enable MAB-based energy-aware priority

class MyLBController:
    def __init__(self, program_name="load_balance", grpc_addr="127.0.0.1:50052"):
        self.server_stats = {}
        self.current_allocations = {}
        self.installed_keys = {}
        self.last_priority = None  # Tracks the last routing decision

        self.mab_counts = {}
        self.mab_explored = set()
        self.mab_values = {}
        self.mab_total_pulls = 0
        # -----------------------------------

        logger.info("Initializing BFRT Connection...")
        self.client_id = 0
        self.device_id = 0

        self.bfrt_interface = gc.ClientInterface(
            grpc_addr, self.client_id, self.device_id
        )
        self.bfrt_interface.bind_pipeline_config(program_name)
        self.bfrt_info = self.bfrt_interface.bfrt_info_get(program_name)
        self.target = gc.Target(device_id=self.device_id, pipe_id=0xFFFF)

        self.egress_table = self.bfrt_info.table_get("pipe.SwitchEgress.send_frame")
        self.nat_table = self.bfrt_info.table_get("pipe.SwitchIngress.server_src_nat")
        self.ecmp_table = self.bfrt_info.table_get("pipe.SwitchIngress.ecmp_nhop")

        logger.info("Switch Programmed Successfully.")

        self.install_egress_rewrite_rules()
        self.install_return_path_rule()

        logger.info("Initializing Default Forwarding Rules (h2)...")
        default_servers = [("h2", 0)]
        self.update_switch_tables(default_servers)

        self.verify_table_state()

        logger.info("Controller is ready and listening.")
        self.run_listener()

    def mac_to_bytes(self, mac_str):
        return bytearray.fromhex(mac_str.replace(":", ""))

    def install_egress_rewrite_rules(self):
        logger.info("Installing Egress Rewrite Rules...")
        port_mac_map = {
            40: "00:00:00:00:01:01",
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
                self.egress_table.entry_add(self.target, [key], [data])
                logger.info(f"   > Egress Rule Added: Port {port} -> SMAC {smac}")
            except Exception as e:
                if "ALREADY_EXISTS" in str(e):
                    self.egress_table.entry_mod(self.target, [key], [data])
                else:
                    logger.error(f"Error on Port {port}: {e}")

    def install_return_path_rule(self):
        logger.info("Installing Fixed Return Path Rules...")
        client_ip = "10.0.3.3"
        client_port = 40
        client_mac = "94:6d:ae:5c:86:b2"
        servers = ["10.0.1.2", "10.0.1.1"]

        for server_ip in servers:
            key = self.nat_table.make_key(
                [
                    gc.KeyTuple("hdr.ipv4.srcAddr", self.ipv4_to_bytes(server_ip)),
                    gc.KeyTuple("hdr.ipv4.dstAddr", self.ipv4_to_bytes(client_ip)),
                ]
            )
            data = self.nat_table.make_data(
                [
                    gc.DataTuple("client_mac", self.mac_to_bytes(client_mac)),
                    gc.DataTuple("port", client_port),
                ],
                "SwitchIngress.nat_reply_to_client",
            )

            try:
                self.nat_table.entry_add(self.target, [key], [data])
                logger.info(
                    f"   > Return Rule Added: Src {server_ip} -> Dst {client_ip}"
                )
            except Exception as e:
                if "ALREADY_EXISTS" not in str(e):
                    logger.error(f"Error installing return path: {e}")

    def update_switch_tables(self, priority_list):
        server_info = {
            "h2": {"ip": "10.0.1.1", "mac": "94:6d:ae:5c:87:72", "port": 132},
            "h3": {"ip": "10.0.1.2", "mac": "94:6d:ae:5d:fd:9c", "port": 180},
        }

        for index, server_tuple in enumerate(priority_list):
            hostname = server_tuple[0]
            if hostname not in server_info:
                logger.warning(f"Unknown hostname '{hostname}' in priority list")
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
                    continue  # Elide "no change" logs
                elif current is not None:
                    self.ecmp_table.entry_mod(self.target, [key], [data])
                    logger.info(
                        f"Switch Hardware Mod | Index {index}: {current} -> {hostname}"
                    )
                else:
                    self.ecmp_table.entry_add(self.target, [key], [data])
                    logger.info(f"Switch Hardware Add | Index {index}: -> {hostname}")

                self.installed_keys[index] = hostname
            except Exception as e_insert:
                logger.error(f"CRITICAL ERROR writing Index {index}: {e_insert}")

    def verify_table_state(self):
        tables_to_check = [
            ("MyIngress.ecmp_nhop", self.ecmp_table),
            ("MyIngress.server_src_nat", self.nat_table),
        ]
        for name, table in tables_to_check:
            try:
                count = sum(1 for _ in table.entry_get(self.target))
                if count == 0:
                    logger.warning(f"Verification: Table {name} is EMPTY!")
                else:
                    logger.info(f"Verification: Table {name} has {count} entries.")
            except Exception as e:
                logger.error(f"Failed to read {name}: {e}")

    def run_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))

        while True:
            data, addr = sock.recvfrom(1024)
            try:
                msg = data.decode().strip()
                host, score, util = msg.split(",")
                score, util = float(score), float(util)

                self.server_stats[host] = (score, util)
                self.update_mab_state(host, reward=score)

                # Periodic evaluation happens silently
                self.recompute_and_update()
            except Exception as e:
                logger.error(f"Error parsing message: {e}")

    def update_mab_state(self, host, reward):
        logger.info(f"MAB Update | Host: {host}, Reward: {reward}")
        if host not in self.mab_counts:
            logger.info(f"New Host Detected in MAB: {host}")
            self.mab_counts[host] = 0
            self.mab_values[host] = 0.0

        if reward > 0:
            logger.info(f"Positive Reward for {host}: {reward:.4f}")
            self.mab_explored.add(host)
            self.mab_values[host] = reward

    def mab_priority(self, N):
        ucb_scores = []
        for host, (score, util) in self.server_stats.items():
            if host not in self.mab_explored:
                logger.info(f"Host {host} has not been explored yet. Assigning infinite UCB for exploration.")
                ucb_scores.append((host, float("inf")))
                continue

            exploitation = self.mab_values[host]
            exploration = 0
            if self.mab_total_pulls > 1 and self.mab_counts[host] > 0:
                exploration = math.sqrt(
                    (2 * math.log(self.mab_total_pulls)) / float(self.mab_counts[host])
                )

            ucb = exploitation + exploration
            logger.info("MAB UCB Score | Host: %s, Exploitation: %.4f, Exploration: %.4f, UCB: %.4f",
                        host, exploitation, exploration, ucb)
            if util < 95.0:
                ucb_scores.append((host, ucb))

        ucb_scores.sort(key=lambda x: x[1], reverse=True)
        
        logger.info("MAB UCB Scores | " + ", ".join([f"{h}: {s:.4f}" for h, s in ucb_scores]))
        
        ordered = ucb_scores[:N]

        if ordered:
            logger.info(f"MAB Selected Hosts: {[h for h, _ in ordered]}")
            first_host = ordered[0][0]
            self.mab_counts[first_host] += 1
            self.mab_total_pulls += 1

        return ordered

    def recompute_and_update(self, N=1):
        ordered = self.performance_only_priority(N)
        # ordered = self.mab_priority(N)

        if ordered:
            # Extract just the hostnames to check for state changes
            current_priority = [x[0] for x in ordered]

            # Only log and push to switch if the decision actually changed
            if current_priority != self.last_priority:
                logger.info(f"Policy Shift | New Priority Order: {current_priority}")
                self.update_switch_tables(ordered)
                self.last_priority = current_priority

    def performance_only_priority(self, N):
        allServers = []
        for host, (score, util) in self.server_stats.items():
            allServers.append((host, util))

        allServers.sort(key=lambda x: x[1], reverse=False)
        return allServers[:N]

    def ipv4_to_bytes(self, ip_str):
        import socket

        return bytearray(socket.inet_aton(ip_str))


if __name__ == "__main__":
    if PERFORMANCE_POLICY:
        logger.info("Starting Load Balancer Controller. Using Performance-Only Priority")
    else:
        logger.info("Starting Load Balancer Controller. Using MAB-Based Energy-Aware Priority")
    ctrl = MyLBController(program_name="load_balance", grpc_addr="127.0.0.1:50052")
