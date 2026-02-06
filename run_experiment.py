import os
import time
import subprocess
import sys

# Import P4 Tutorial Utils
sys.path.append('/home/p4/tutorials/utils')
import p4runtime_lib.bmv2
import p4runtime_lib.helper
from p4runtime_switch import P4RuntimeSwitch

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from p4_mininet import P4Host

# --- Configuration ---
P4_SOURCE = "p4src/load_balance.p4"
BUILD_DIR = "build"
JSON_FILE = f"{BUILD_DIR}/load_balance.json"
P4INFO_FILE = f"{BUILD_DIR}/load_balance.p4info.txtpb"

HOST_CONFIG = {
    "h1": (1, "10.0.1.1", "08:00:00:00:01:01"),
    "h2": (2, "10.0.2.2", "08:00:00:00:02:02"),
    "h3": (3, "10.0.3.3", "08:00:00:00:03:03"),
}

class EnergyTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)
        
        # P4RuntimeSwitch handles the gRPC port automatically
        s1 = self.addSwitch('s1', 
                            cls=P4RuntimeSwitch, 
                            sw_path='simple_switch_grpc',
                            json_path=JSON_FILE, 
                            thrift_port=9090,
                            grpc_port=50051,
                            pcap_dump='pcaps',
                            log_console=True)

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
    for host_name, config in HOST_CONFIG.items():
        h = net.get(host_name)
        h.cmd("ip route add 10.0.0.1 dev eth0")
        h.cmd("arp -s 10.0.0.1 08:00:00:00:01:01")
        h.cmd("ethtool --offload eth0 rx off tx off")

def program_switch_manually(net):
    """
    Directly programs the switch using P4Runtime.
    This ensures the pipeline is set BEFORE the controller starts.
    """
    info("--- Programming Switch (Direct P4Runtime) ---\n")
    sw = net.get('s1')
    
    try:
        p4info_helper = p4runtime_lib.helper.P4InfoHelper(P4INFO_FILE)
        s1_conn = p4runtime_lib.bmv2.Bmv2SwitchConnection(
            name='s1',
            address=f'127.0.0.1:{sw.grpc_port}',
            device_id=0
        )
        s1_conn.MasterArbitrationUpdate()
        
        # Set Pipeline (The "Push")
        s1_conn.SetForwardingPipelineConfig(
            p4info=p4info_helper.p4info,
            bmv2_json_file_path=JSON_FILE
        )
        info("--- Switch Programmed Successfully ---\n")
        
    except Exception as e:
        info(f"!!! Error Programming Switch: {e}\n")
        sys.exit(1)

def run_experiment():
    compile_p4()
    os.makedirs("logs", exist_ok=True)
    os.makedirs("pcaps", exist_ok=True)

    topo = EnergyTopo()
    net = Mininet(topo=topo, host=P4Host, link=TCLink, controller=None)
    net.start()
    configure_network(net)

    # Program the switch
    program_switch_manually(net)

    # --- CONTROLLER REMOVED ---
    # We do NOT start energy_aware_controller.py here.
    # You will run it manually in another terminal.
    
    # Start Agents (These are still useful to keep automatic)
    info("--- Starting Agents ---\n")
    agent_procs = []
    for host_name in HOST_CONFIG:
        if host_name == "h1": continue
        log_file = open(f"logs/{host_name}_agent.log", "w")
        p = subprocess.Popen([sys.executable, "server_agent.py", host_name], 
                             stdout=log_file, stderr=log_file)
        agent_procs.append(p)

    info("\n*** Ready. Start your controller manually now! ***\n")
    
    CLI(net)

    for p in agent_procs: p.terminate()
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_experiment()