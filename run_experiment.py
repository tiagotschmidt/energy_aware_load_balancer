import os
import time
import subprocess
import sys

# Import P4 Tutorial Utils
sys.path.append('/home/p4/tutorials/utils')
from p4runtime_switch import P4RuntimeSwitch
from p4_mininet import P4Host

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

# --- Configuration ---
P4_SOURCE = "p4src/load_balance.p4"
BUILD_DIR = "build"
JSON_FILE = f"{BUILD_DIR}/load_balance.json"
P4INFO_FILE = f"{BUILD_DIR}/load_balance.p4.p4info.txtpb"
GRPC_PORT = 50051 

HOST_CONFIG = {
    "h1": (1, "10.0.1.1", "08:00:00:00:01:01"),
    "h2": (2, "10.0.2.2", "08:00:00:00:02:02"),
    "h3": (3, "10.0.3.3", "08:00:00:00:03:03"),
}

def get_log_path(name):
    return f"{os.getcwd()}/logs/{name}.log"

class EnergyTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)
        s1_log = get_log_path("s1")
        
        # P4RuntimeSwitch handles the gRPC port automatically
        s1 = self.addSwitch('s1', 
                            cls=P4RuntimeSwitch, 
                            sw_path='simple_switch_grpc',
                            json_path=JSON_FILE, 
                            thrift_port=9090,
                            grpc_port=50051,
                            pcap_dump='pcaps',
                            log_console=True,
                            log_file=get_log_path("s1"))

        for host_name, config in HOST_CONFIG.items():
            core_id, ip_addr, mac_addr = config
            self.addHost(host_name, 
                         cls=CPULimitedHost, 
                         cores=str(core_id),
                         ip=ip_addr, 
                         mac=mac_addr)
            self.addLink(host_name, s1, port2=core_id)

def compile_p4():
    info("--- Compiling P4 Code ---\n")
    if not os.path.exists(BUILD_DIR): os.makedirs(BUILD_DIR)
    cmd = f"p4c-bm2-ss --p4v 16 --p4runtime-files {P4INFO_FILE} -o {JSON_FILE} {P4_SOURCE}"
    if subprocess.call(cmd, shell=True) != 0:
        info("!!! Compilation Failed. Exiting.\n")
        sys.exit(1)

def configure_network(net):
    info("--- Configuring Gateway & ARP ---\n")
    h1 = net.get("h1")
    h1.cmd("ip route add 10.0.0.1 dev eth0")
    h1.cmd("arp -s 10.0.0.1 00:00:00:00:01:01") 

    h2 = net.get("h2")
    h2.cmd("ip route add 10.0.1.0/24 dev eth0") 
    h2.cmd("arp -s 10.0.1.1 00:00:00:00:02:02") 

    h3 = net.get("h3")
    h3.cmd("ip route add 10.0.1.0/24 dev eth0")
    h3.cmd("arp -s 10.0.1.1 00:00:00:00:03:03")

    for h in net.hosts:
        h.cmd("ethtool --offload eth0 rx off tx off")

def run_experiment():
    compile_p4()
    if not os.path.exists("logs"): os.makedirs("logs")
    if not os.path.exists("pcaps"): os.makedirs("pcaps")

    topo = EnergyTopo()
    net = Mininet(topo=topo, host=P4Host, link=TCLink, controller=None)
    net.start()
    configure_network(net)

    # REMOVED: program_switch_manually()
    # The controller will handle P4 pipeline configuration now.

    info("--- Starting Agents ---\n")
    agent_procs = []
    for host_name, config in HOST_CONFIG.items():
        if host_name == "h1": continue
        core_id = config[0]
        h = net.get(host_name)
        log_file = open(f"logs/{host_name}_agent.log", "w")
        # p = h.popen([sys.executable, "server_agent.py", host_name, str(core_id)], 
        #             stdout=log_file, stderr=log_file)
        # agent_procs.append(p)

    info(f"\n*** Infrastructure Ready. Start 'energy_aware_controller.py' in another terminal! ***\n")
    
    CLI(net)

    for p in agent_procs: p.terminate()
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_experiment()