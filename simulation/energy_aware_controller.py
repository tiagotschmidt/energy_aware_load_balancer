import os
import argparse
import socket
import sys
import math
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Set
import datetime
from collections import deque

# --- P4Runtime Setup ---
P4_UTILS_PATH = "/home/p4/tutorials/utils"
if P4_UTILS_PATH not in sys.path:
    sys.path.append(P4_UTILS_PATH)

try:
    import p4runtime_lib
    import p4runtime_lib.bmv2 as bmv2
    import p4runtime_lib.helper as helper
    from p4.v1 import p4runtime_pb2 as p4runtime_pb2
except ImportError as e:
    print(f"--- ERROR: Could not find P4 modules: {e} ---")
    sys.exit(1)

BUILD_DIR = "simulation"
JSON_FILE = f"{BUILD_DIR}/load_balance.json/load_balance.json"
P4INFO_FILE = f"{BUILD_DIR}/load_balance.p4info.txtpb"
GRPC_PORT = 50051
NUMBER_SWITCH_TABLE_ENTRIES = 20

# --- Configure Logging ---
log_filename = f"lb_controller_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    filename=log_filename,
    filemode="w",
)
logger = logging.getLogger("LB_Ctrl")


# ---------------------------------------------------------
# POLICIES AND ESTIMATORS (From Tofino Script)
# ---------------------------------------------------------
class ServerStat:
    def __init__(self, host: str, score: float, util: float, power: float):
        self.host = host
        self.score = score
        self.util = util
        self.power = power

    @classmethod
    def parse(cls, raw_msg: str) -> "ServerStat":
        parts = raw_msg.strip().split(",")
        if len(parts) != 4:
            raise ValueError(f"Expected format 'host,score,util,power', got: {raw_msg}")

        return cls(
            host=parts[0].strip(),
            score=float(parts[1].strip()),
            util=float(parts[2].strip()),
            power=float(parts[3].strip()),
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


class RoundRobinPolicy(LoadBalancingPolicy):
    def observe(self, stat: ServerStat) -> None:
        pass

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        if not stats:
            return []
        hosts = list(stats.keys())
        return [hosts[i % len(hosts)] for i in range(n_servers)]


class LeastUtilizedPolicy(LoadBalancingPolicy):
    def observe(self, stat: ServerStat) -> None:
        pass

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        if not stats:
            return []
        ordered = sorted(stats.values(), key=lambda s: s.util)
        logger.info(
            f"LeastUtilized Evaluation | Server Scores: {[f'{s.host} (util={s.util:.1f}%)' for s in ordered]}"
        )
        selected_servers = [s.host for s in ordered[:1]]

        if len(selected_servers) < n_servers:
            selected_servers.extend(
                selected_servers[-1] for _ in range(n_servers - len(selected_servers))
            )
        return selected_servers


class HostEstimator:
    def __init__(self, host: str, window_size: int = 20):
        self.host = host
        self.history = deque(maxlen=window_size)

    def add_sample(self, util: float, power: float):
        self.history.append((util, power))

    @property
    def sample_count(self) -> int:
        return len(self.history)

    def get_marginal_cost(self) -> float:
        n = len(self.history)
        if n < 2:
            return 1.0

        sum_x = sum(u for u, p in self.history)
        sum_y = sum(p for u, p in self.history)
        sum_xx = sum(u * u for u, p in self.history)
        sum_xy = sum(u * p for u, p in self.history)

        denominator = (n * sum_xx) - (sum_x * sum_x)
        if denominator == 0:
            return 1.0

        slope = ((n * sum_xy) - (sum_x * sum_y)) / denominator
        if slope <= 0:
            return 1.0

        return min(max(slope, 0.1), 15.0)


class MarginalCostPolicy(LoadBalancingPolicy):
    def __init__(self, bootstrap_samples: int = 5, window_size: int = 5):
        self.estimators: Dict[str, HostEstimator] = {}
        self.bootstrap_samples = bootstrap_samples
        self.window_size = window_size

    def observe(self, stat: ServerStat) -> None:
        if stat.host not in self.estimators:
            self.estimators[stat.host] = HostEstimator(stat.host, self.window_size)
        self.estimators[stat.host].add_sample(stat.util, stat.power)

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        if not stats:
            return []
        available_hosts = [host for host, stat in stats.items()]

        raw_total_utilization = sum(stats[host].util for host in available_hosts)
        for host in available_hosts:
            estimator = self.estimators.get(host)
            if estimator is None or estimator.sample_count < self.bootstrap_samples:
                return [host] * n_servers

        average_cluster_utilization = (
            raw_total_utilization / len(available_hosts) / 100.0
            if available_hosts
            else 0.0
        )

        if average_cluster_utilization > 0.85:
            return [available_hosts[i % len(available_hosts)] for i in range(n_servers)]

        marginal_costs = []
        total_weights = 0.0

        for host in available_hosts:
            cost = self.estimators[host].get_marginal_cost()
            EPSILON = 1e-6
            efficiency_score = 1 / (cost + EPSILON)
            efficiency_score = efficiency_score * efficiency_score

            host_utilization = max(0.0, min(1.0, stats[host].util / 100))
            lu_penalty = max(0.001, 1.0 - host_utilization)
            lu_penalty = lu_penalty * lu_penalty

            weight = efficiency_score * pow(
                lu_penalty, 1.0 + average_cluster_utilization
            )
            total_weights += weight
            marginal_costs.append((host, cost, weight))

        marginal_costs.sort(key=lambda x: x[1])

        if total_weights == 0:
            return [available_hosts[i % len(available_hosts)] for i in range(n_servers)]

        selected_servers = []
        remainders = []
        for host, cost, weight in marginal_costs:
            exact_share = weight / total_weights * n_servers
            allocated = int(exact_share)
            selected_servers.extend([host] * allocated)
            remainders.append((host, exact_share - allocated))

        remainders.sort(key=lambda x: x[1], reverse=True)
        for host, _ in remainders:
            if len(selected_servers) >= n_servers:
                break
            selected_servers.append(host)

        while len(selected_servers) < n_servers:
            selected_servers.append(
                available_hosts[len(selected_servers) % len(available_hosts)]
            )

        return selected_servers


PULLS_BEFORE_EXPLORATION = 5


class MabPolicy(LoadBalancingPolicy):
    def __init__(self):
        self.mab_counts: Dict[str, Dict[str, float]] = {}
        self.mab_explored: Dict[str, Set[str]] = {
            "low": set(),
            "medium": set(),
            "high": set(),
        }
        self.mab_values: Dict[str, Dict[str, float]] = {}
        self.mab_total_pulls: int = 0

    def get_bucket(self, util):
        if util < 40:
            return "low"
        elif util < 80:
            return "medium"
        else:
            return "high"

    def observe(self, stat: ServerStat) -> None:
        if stat.host not in self.mab_counts:
            self.mab_counts[stat.host] = {"low": 0.0, "medium": 0.0, "high": 0.0}
            self.mab_values[stat.host] = {"low": 0.0, "medium": 0.0, "high": 0.0}

        if stat.score > 0:
            bucket = self.get_bucket(stat.util)
            self.mab_explored[bucket].add(stat.host)
            if stat.score > self.mab_values[stat.host][bucket]:
                self.mab_values[stat.host][bucket] = stat.score

    def evaluate(
        self, stats: Dict[str, ServerStat], util: float, n_servers: int
    ) -> List[str]:
        if not stats:
            return []
        ucb_scores = []
        bucket = self.get_bucket(util)

        for host, stat in stats.items():
            if self.mab_counts[host][bucket] < PULLS_BEFORE_EXPLORATION:
                ucb_scores.append((host, float("inf"), self.mab_counts[host][bucket]))
                continue

            exploitation = self.mab_values[host][bucket]
            exploration = 0
            if self.mab_total_pulls > 1 and self.mab_counts[host][bucket] > 0:
                exploration = math.sqrt(
                    (2 * math.log(self.mab_total_pulls))
                    / float(self.mab_counts[host][bucket])
                )

            ucb = exploitation + exploration
            if stat.util < 95.0:
                ucb_scores.append((host, ucb, self.mab_counts[host][bucket]))

        ucb_scores.sort(key=lambda x: x[1], reverse=True)
        ordered_hosts = [x[0] for x in ucb_scores[:1]]

        if ordered_hosts:
            first_host = ordered_hosts[0]
            if first_host in self.mab_explored[bucket]:
                self.mab_counts[first_host][bucket] += 1
                self.mab_total_pulls += 1

        if len(ordered_hosts) < n_servers:
            ordered_hosts.extend(
                ordered_hosts[0] for _ in range(n_servers - len(ordered_hosts))
            )

        return ordered_hosts


# ---------------------------------------------------------
# THE CONTROLLER (P4Runtime adapted)
# ---------------------------------------------------------
class MyLBController:
    def __init__(
        self, policy: LoadBalancingPolicy, p4info_path, bmv2_json_path, num_hosts
    ):
        self.policy = policy
        self.num_hosts = num_hosts
        self.p4info_helper = helper.P4InfoHelper(p4info_path)
        self.bmv2_json_path = bmv2_json_path
        self.server_stats: Dict[str, ServerStat] = {}
        self.installed_keys = {}
        self.last_priority = None

        self.s1_conn = p4runtime_lib.bmv2.Bmv2SwitchConnection(
            name="s1", address=f"127.0.0.1:{GRPC_PORT}", device_id=0
        )
        self.s1_conn.MasterArbitrationUpdate()

        self.s1_conn.SetForwardingPipelineConfig(
            p4info=self.p4info_helper.p4info, bmv2_json_file_path=self.bmv2_json_path
        )
        print("--- Switch Programmed Successfully ---\n")
        logger.info("Switch Programmed Successfully.")

        self.install_egress_rewrite_rules()
        self.install_return_path_rule()

        print(
            f"Initializing Default Forwarding Rules for {self.num_hosts - 1} servers..."
        )
        # Start at 2 since h1 is the client
        default_servers = [f"h{i}" for i in range(2, self.num_hosts + 1)]
        expanded_defaults = [
            default_servers[i % len(default_servers)]
            for i in range(NUMBER_SWITCH_TABLE_ENTRIES)
        ]
        self.update_switch_tables(expanded_defaults)

        self.verify_table_state()

        print("Controller is ready and listening.")
        logger.info("Controller is ready and listening.")
        self.run_listener()

    def install_egress_rewrite_rules(self):
        print(f"Installing Egress Rewrite Rules for {self.num_hosts} hosts...")
        for port in range(1, self.num_hosts + 1):
            smac = f"00:00:00:00:00:{port:02x}"
            entry = self.p4info_helper.buildTableEntry(
                table_name="MyEgress.send_frame",
                match_fields={"standard_metadata.egress_port": port},
                action_name="MyEgress.rewrite_mac",
                action_params={"smac": smac},
            )
            try:
                self.s1_conn.WriteTableEntry(entry)
            except Exception as e:
                if "ALREADY_EXISTS" not in str(e):
                    logger.error(f"Error installing egress rule: {e}")

    def install_return_path_rule(self):
        print("Installing Fixed Return Path Rules (Server IP -> Client IP)...")
        client_ip = "10.0.0.100"
        client_port = 1
        client_mac = "00:00:00:00:00:01"
        servers = [f"10.0.0.{i}" for i in range(2, self.num_hosts + 1)]

        for server_ip in servers:
            entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.server_src_nat",
                match_fields={
                    "hdr.ipv4.srcAddr": server_ip,
                    "hdr.ipv4.dstAddr": client_ip,
                },
                action_name="MyIngress.nat_reply_to_client",
                action_params={"client_mac": client_mac, "port": client_port},
            )
            try:
                self.s1_conn.WriteTableEntry(entry)
            except Exception as e:
                if "ALREADY_EXISTS" not in str(e):
                    logger.error(f"Error installing return rule: {e}")

    def update_switch_tables(self, priority_hosts: List[str]):
        server_info = {
            f"h{i}": {"ip": f"10.0.0.{i}", "mac": f"00:00:00:00:00:{i:02x}", "port": i}
            for i in range(2, self.num_hosts + 1)
        }

        for index, hostname in enumerate(priority_hosts):
            if index >= NUMBER_SWITCH_TABLE_ENTRIES:
                break
            if hostname not in server_info:
                continue

            info = server_info[hostname]
            new_entry = self.p4info_helper.buildTableEntry(
                table_name="MyIngress.ecmp_nhop",
                match_fields={"meta.ecmp_select": index},
                action_name="MyIngress.forward_to_server",
                action_params={
                    "server_mac": info["mac"],
                    "server_ip": info["ip"],
                    "port": info["port"],
                },
            )

            current = self.installed_keys.get(index)

            try:
                if current == hostname:
                    continue
                elif current is not None:
                    request = p4runtime_pb2.WriteRequest()
                    request.device_id = self.s1_conn.device_id
                    request.election_id.low = 1
                    update = request.updates.add()
                    update.type = p4runtime_pb2.Update.MODIFY
                    update.entity.table_entry.CopyFrom(new_entry)
                    self.s1_conn.client_stub.Write(request)
                    logger.info(
                        f"Switch Hardware Mod | Index {index}: {current} -> {hostname}"
                    )
                else:
                    self.s1_conn.WriteTableEntry(new_entry)
                    logger.info(f"Switch Hardware Add | Index {index}: -> {hostname}")

                self.installed_keys[index] = hostname
            except Exception as e_insert:
                logger.error(f"CRITICAL ERROR writing Index {index}: {e_insert}")

    def verify_table_state(self):
        print("\n--- VERIFYING SWITCH STATE ---")
        tables = ["MyIngress.ecmp_nhop", "MyIngress.server_src_nat"]
        for table_name in tables:
            try:
                table_id = self.p4info_helper.get_tables_id(table_name)
                count = 0
                for response in self.s1_conn.ReadTableEntries(
                    table_id=table_id, dry_run=False
                ):
                    for _ in response.entities:
                        count += 1

                if count == 0:
                    print(f"  [WARNING] Table {table_name} is EMPTY! (Write Failed)")
                else:
                    print(f"  [OK] Table {table_name} has {count} entries.")
            except Exception as e:
                print(f"  [ERROR] Failed to read {table_name}: {e}")
        print("------------------------------\n")

    def run_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 50001))

        while True:
            data, addr = sock.recvfrom(1024)
            try:
                stat = ServerStat.parse(data.decode())
                self.server_stats[stat.host] = stat
                self.policy.observe(stat)

                ordered_hosts = self.policy.evaluate(
                    self.server_stats, stat.util, n_servers=NUMBER_SWITCH_TABLE_ENTRIES
                )

                if ordered_hosts and ordered_hosts != self.last_priority:
                    logger.info(f"Policy Shift | New Priority Order: {ordered_hosts}")
                    self.update_switch_tables(ordered_hosts)
                    self.last_priority = ordered_hosts

            except ValueError as ve:
                logger.error(f"Parse Error (rejected invalid input): {ve}")
            except Exception as e:
                logger.error(f"Unexpected execution error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P4 Load Balancer Controller")
    parser.add_argument(
        "--hosts",
        type=int,
        default=8,
        help="Total number of hosts (h1 is client, the rest are servers)",
    )
    args = parser.parse_args()

    # -----------------------------------------------------------------
    # SELECT YOUR ACTIVE POLICY HERE
    # -----------------------------------------------------------------
    # active_policy = LeastUtilizedPolicy()
    active_policy = RoundRobinPolicy()
    print(
        f"Starting Load Balancer Controller for {args.hosts} hosts. Using Round-Robin Priority."
    )
    # active_policy = MabPolicy()

    #active_policy = MarginalCostPolicy()
    #print(
    #    f"Starting Load Balancer Controller for {args.hosts} hosts. Using Marginal-cost Energy-Aware Priority."
    #)

    ctrl = MyLBController(active_policy, P4INFO_FILE, JSON_FILE, num_hosts=args.hosts)
