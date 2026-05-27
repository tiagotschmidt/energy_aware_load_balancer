import os
import socket
import math
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Set
LOCAL_MODE = os.getenv("LOCAL_MODE", "0") == "1"
if not LOCAL_MODE:
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

PERFORMANCE_POLICY = False  # Set to False to enable MAB-based energy-aware priority
PULLS_BEFORE_EXPLORATION = (
    5  # Number of times to pull a host before considering it "explored" in MAB
)


class ServerStat:
    def __init__(self, host: str, score: float, util: float):
        self.host = host
        self.score = score
        self.util = util

    @classmethod
    def parse(cls, raw_msg: str) -> "ServerStat":
        parts = raw_msg.strip().split(",")
        if len(parts) != 3:
            raise ValueError(f"Expected format 'host,score,util', got: {raw_msg}")

        return cls(
            host=parts[0].strip(),
            score=float(parts[1].strip()),
            util=float(parts[2].strip()),
        )


class LoadBalancingPolicy(ABC):
    @abstractmethod
    def observe(self, stat: ServerStat) -> None:
        pass

    @abstractmethod
    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        pass


class PerformanceOnlyPolicy:
    def observe(self, stat: ServerStat) -> None:
        pass

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        ordered = sorted(stats.values(), key=lambda s: s.util)
        logger.info(
            f"Performance-Only Evaluation | Server Scores: {[f'{s.host} (util={s.util:.1f}%)' for s in ordered]}"
        )
        selected_servers = [s.host for s in ordered[:n_servers]]
        logger.info(f"Performance-Only Evaluation | Selected: {selected_servers}")
        return selected_servers


class EnergyAwarePolicy:
    def observe(self, stat: ServerStat) -> None:
        pass

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        available = [s for s in stats.values() if s.util < 70.0]
        busy = [s for s in stats.values() if s.util >= 70.0]

        available.sort(key=lambda s: s.score, reverse=True)
        busy.sort(key=lambda s: s.score, reverse=True)

        ordered = available + busy
        logger.info(
            f"Energy-Aware Evaluation | Available: {[s.host for s in available]}, Busy: {[s.host for s in busy]}"
        )
        selected_servers = [s.host for s in ordered[:n_servers]]
        logger.info(
            f"Energy-Aware Evaluation | Available: {[s.host for s in available]}, Busy: {[s.host for s in busy]}, Selected: {selected_servers}"
        )
        return selected_servers


class MabPolicy:
    def __init__(self):
        self.mab_counts: Dict[str, int] = {}
        self.mab_explored: Dict[str, Set[str]] = {}
        self.mab_values: Dict[str, float] = {}
        self.mab_total_pulls: int = 0

        self.mab_explored["low"] = set()
        self.mab_explored["medium"] = set()
        self.mab_explored["high"] = set()

    def get_bucket(self, util):
        if util < 40:
            return "low"
        elif util < 80:
            return "medium"
        else:
            return "high"

    def observe(self, stat: ServerStat) -> None:
        logger.info(
            f"MAB Observe | Host: {stat.host}, Score: {stat.score:.4f}, Util: {stat.util:.1f}%"
        )
        if stat.host not in self.mab_counts:
            logger.info(f"New Host Detected in MAB: {stat.host}")
            self.mab_counts[stat.host] = {"low": 0.0, "medium": 0.0, "high": 0.0}
            self.mab_values[stat.host] = {"low": 0.0, "medium": 0.0, "high": 0.0}

        if stat.score > 0:
            bucket = self.get_bucket(stat.util)
            logger.info(f"Positive Score for {stat.host}: {stat.score:.4f}")
            self.mab_explored[bucket].add(stat.host)

            bucket = self.get_bucket(stat.util)
            if stat.score > self.mab_values[stat.host][bucket]:
                logger.info(
                    f"Updating MAB Value for {stat.host}{bucket} from {self.mab_values[stat.host]} to {stat.score:.4f}"
                )
                self.mab_values[stat.host][bucket] = stat.score

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        ucb_scores = []
        bucket = self.get_bucket(util)

        for host, stat in stats.items():
            if self.mab_counts[host][bucket] < PULLS_BEFORE_EXPLORATION:
                logger.info(
                    f"Host {host} has not been explored yet. Assigning infinite UCB for exploration."
                )
                logger.info(f"Host {host} has been pulled {self.mab_counts[host][bucket]} times, which is less than the threshold of {PULLS_BEFORE_EXPLORATION}.")
                ucb_scores.append((host, float("inf"), self.mab_counts[host][bucket]))
                continue

            exploitation = self.mab_values[host][bucket]
            exploration = 0
            if (
                self.mab_total_pulls > 1 and self.mab_counts[host][bucket] > 0
            ):  # Protected div by zero
                exploration = math.sqrt(
                    (2 * math.log(self.mab_total_pulls)) / float(self.mab_counts[host][bucket])
                )

            ucb = exploitation + exploration
            logger.info(
                f"MAB UCB Calculation | Host: {host}, Bucket: {bucket}, Exploitation: {exploitation:.4f}, Exploration: {exploration:.4f}, UCB: {ucb:.4f}"
            )
            if stat.util < 95.0:
                ucb_scores.append((host, ucb, self.mab_counts[host][bucket]))

        ucb_scores.sort(key=lambda x: x[1], reverse=True)
        logger.info(
            f"MAB Evaluation | UCB Scores: {[f'{h} (UCB={s:.4f}, Pulls={c})' for h, s, c in ucb_scores]}"
        )
        ordered_hosts = [x[0] for x in ucb_scores[:n_servers]]

        explored_first_host = (
            ordered_hosts[0] and ordered_hosts[0] in self.mab_explored[bucket]
        )

        if ordered_hosts:
            logger.info(f"MAB Evaluation | Selected Host: {ordered_hosts[0]}")
            first_host = ordered_hosts[0]

            if explored_first_host:
                logger.info(f"MAB Update | Incrementing count for {first_host}")
                self.mab_counts[first_host][bucket] += 1
                self.mab_total_pulls += 1

        logger.debug(f"MAB Algorithm Evaluated Priority: {ordered_hosts}")
        return ordered_hosts


# ---------------------------------------------------------
# THE CONTROLLER
# ---------------------------------------------------------
class MyLBController:
    def __init__(
        self,
        policy: LoadBalancingPolicy,
        program_name="load_balance",
        grpc_addr="127.0.0.1:50052",
    ):
        self.server_stats: Dict[str, ServerStat] = {}
        self.current_allocations = {}
        self.installed_keys = {}
        self.last_priority = None  # Tracks the last routing decision
        self.policy = policy

        if LOCAL_MODE:
            logger.info("Running in LOCAL MODE. Switch hardware interactions are disabled.")
        else:
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
            self.update_switch_tables(["h2"])
            self.verify_table_state()

        logger.info("Controller is ready and listening.")
        self.run_listener()

    def ipv4_to_bytes(self, ip_str):
        import socket

        return bytearray(socket.inet_aton(ip_str))

    def verify_table_state(self):
        logger.info("VERIFYING SWITCH STATE...")
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

    def update_switch_tables(self, priority_hosts: List[str]):
        if LOCAL_MODE:
            # Just log the state changes in local mode
            logger.info(f"[LOCAL STUB] Switch hardware would be updated with: {priority_hosts}")
            return

        server_info = {
            "h2": {"ip": "10.0.1.1", "mac": "94:6d:ae:5c:87:72", "port": 132},
            "h3": {"ip": "10.0.1.2", "mac": "94:6d:ae:5d:fd:9c", "port": 180},
        }

        for index, hostname in enumerate(priority_hosts):
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

    def run_listener(self):
        logger.info("Starting UDP Listener on Port 50001...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))

        while True:
            data, addr = sock.recvfrom(1024)
            try:
                stat = ServerStat.parse(data.decode())
                self.server_stats[stat.host] = stat
                self.policy.observe(stat)

                # Periodic evaluation happens silently
                ordered_hosts = self.policy.evaluate(
                    self.server_stats, stat.util, n_servers=1
                )

                if ordered_hosts:
                    # Only log and update the hardware if the decision changed
                    if ordered_hosts != self.last_priority:
                        logger.info(
                            f"Policy Shift | New Priority Order: {ordered_hosts}"
                        )
                        self.update_switch_tables(ordered_hosts)
                        self.last_priority = ordered_hosts

            except ValueError as ve:
                logger.error(f"Parse Error (rejected invalid input): {ve}")
            except Exception as e:
                logger.error(f"Unexpected execution error: {e}")


if __name__ == "__main__":
    if PERFORMANCE_POLICY:
        active_policy = PerformanceOnlyPolicy()
        logger.info(
            "Starting Load Balancer Controller. Using Performance-Only Priority"
        )
    else:
        active_policy = MabPolicy()
        logger.info(
            "Starting Load Balancer Controller. Using MAB-Based Energy-Aware Priority"
        )

    ctrl = MyLBController(
        policy=active_policy, program_name="load_balance", grpc_addr="127.0.0.1:50052"
    )
