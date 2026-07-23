#!/usr/bin/env python3
import os
import json
import argparse
import random
import sys

SWITCH_START_TIMEOUT = 10
# Ensure p4-utils is in the path when running from the project root
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "p4-utils", "mininet"))
# Add the parent p4-utils directory to import P4RuntimeSwitch
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "p4-utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "p4-utils", "mininet"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "p4-utils"))

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.cli import CLI
from p4_mininet import P4Switch, P4Host
from p4_mininet import P4Switch

from p4runtime_switch import P4RuntimeSwitch

CONFIG_PATH = "simulation/config/host_profiles.json"


def ensure_host_profiles(num_hosts):
    """Dynamically extends the host_profiles.json if more hosts are requested."""
    profiles = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            profiles = json.load(f)

    updated = False
    for i in range(1, num_hosts + 1):
        host_name = f"h{i}"
        if host_name not in profiles:
            # Assign alternating operator pools for scalability testing
            pool = "prefill" if i % 2 != 0 else "decode"
            profiles[host_name] = {
                "m": round(random.uniform(0.3, 0.7), 2),
                "c": round(random.uniform(30.0, 70.0), 1),
                "pool": pool,
            }
            updated = True

    if updated:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(profiles, f, indent=2)
        print(f"--- Updated {CONFIG_PATH} to support {num_hosts} hosts ---")


class ScalableSimTopo(Topo):
    def build(self, num_hosts=4):
        # Use P4RuntimeSwitch and simple_switch_grpc to enable port 50051
        switch = self.addSwitch(
            "s1",
            cls=P4RuntimeSwitch,
            json_path="simulation/load_balance.json/load_balance.json",
            sw_path="simple_switch_grpc",
            grpc_port=50051,
            thrift_port=9090,
        )

        for i in range(1, num_hosts + 1):
            if i == 1:
                # Client gets a distinct IP to avoid VIP conflict
                host_ip = "10.0.0.100/24"
            else:
                host_ip = f"10.0.0.{i}/24"

            host = self.addHost(
                f"h{i}", cls=P4Host, ip=host_ip, mac=f"00:00:00:00:00:0{i}"
            )
            self.addLink(host, switch)


def run_simulation(num_hosts):
    ensure_host_profiles(num_hosts)

    topo = ScalableSimTopo(num_hosts=num_hosts)
    net = Mininet(topo=topo, controller=None)
    net.start()

    print(f"--- Environment instantiated with {num_hosts} heterogeneous hosts ---")

    h1 = net.get("h1")
    h1.cmd("arp -s 10.0.0.1 08:00:00:00:01:00")

    for i in range(2, num_hosts + 1):
        host = net.get(f"h{i}")

        print(f"Starting Sift and Sim Agent on {host.name}...")
        host.cmd("arp -s 10.0.0.100 00:00:00:00:00:01")

        # Execute Sift server using uv
        host.cmd(
            f"cd sift && ./sift_server --id {host.name} --port 8080  > /tmp/{host.name}_sift_server.log 2>&1 &"
        )

        # Execute Simulated Agent using uv
        host.cmd(
            f"python3 simulation/server_agent/sim_server_agent.py {host.name} --controller-ip 10.0.0.254 > /tmp/{host.name}_agent.log 2>&1 &"
        )

    CLI(net)
    net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P4 Mininet Simulation Setup")
    parser.add_argument(
        "--hosts", type=int, default=4, help="Number of hosts to simulate"
    )
    args = parser.parse_args()

    run_simulation(args.hosts)
