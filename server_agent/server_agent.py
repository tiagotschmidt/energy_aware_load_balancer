import time
import socket
import os
import logging
import argparse
import csv
import glob

# --- Configuration ---
SWITCH_IP = "127.0.0.1"
PORT = 50001
INTERVAL = 0.5         
LOG_DIR = "../sift/logs"
RAPL_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj" # Standard RAPL path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def get_zenpower_path():
    """Searches for the zenpower hwmon directory."""
    for path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            with open(path, 'r') as f:
                if "zenpower" in f.read():
                    return os.path.dirname(path)
        except Exception:
            continue
    return None

def get_cpu_utilization(prev_idle, prev_total):
    """Reads global CPU utilization."""
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline() 
            metrics = list(map(int, line.split()[1:]))
            curr_idle = metrics[3] + metrics[4]
            curr_total = sum(metrics)
        diff_idle = curr_idle - prev_idle
        diff_total = curr_total - prev_total
        if diff_total == 0: return 0.0, curr_idle, curr_total
        return max(0.0, 100.0 * (1.0 - (diff_idle / diff_total))), curr_idle, curr_total
    except Exception as e:
        logging.error(f"CPU Read Error: {e}")
        return 0.0, prev_idle, prev_total

def get_power_watts(driver, hwmon_path, prev_energy, time_delta):
    """
    Selects power reading method based on the driver parameter.
    Returns (power_in_watts, current_energy_reading).
    """
    try:
        if driver == "amd" and hwmon_path:
            # Zenpower: Sum of Core and SoC power in microwatts
            with open(f"{hwmon_path}/power1_input", 'r') as f:
                p_core = int(f.read()) / 1000000.0 
            with open(f"{hwmon_path}/power2_input", 'r') as f:
                p_soc = int(f.read()) / 1000000.0
            return p_core + p_soc, None
        
        elif driver == "intel":
            # RAPL: Differential energy in microjoules converted to Watts
            with open(RAPL_PATH, 'r') as f:
                curr_energy = int(f.read())
            if prev_energy is None:
                return 0.0, curr_energy
            power = (curr_energy - prev_energy) / 1000000.0 / time_delta
            return max(0.0, power), curr_energy
            
    except Exception:
        pass
    return None, None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host_name", help="Name of this host")
    parser.add_argument("--driver", choices=["intel", "amd"], default="intel", 
                        help="Telemetry driver: 'intel' (RAPL) or 'amd' (Zenpower)")
    args = parser.parse_args()

    hwmon_path = get_zenpower_path() if args.driver == "amd" else None
    if args.driver == "amd" and not hwmon_path:
        logging.error("AMD driver selected but Zenpower not found. Falling back to simulation.")

    if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
    csv_file = f"{LOG_DIR}/{args.host_name}_energy.csv"
    
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "host", "cpu_util", "throughput_rps", "power_watts", "efficiency_score"])
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _, prev_idle, prev_total = get_cpu_utilization(0, 0)
    prev_energy = None
    prev_time = time.time()
    
    try:
        while True:
            time.sleep(INTERVAL)
            curr_time = time.time()
            time_delta = curr_time - prev_time
            
            util, curr_idle, curr_total = get_cpu_utilization(prev_idle, prev_total)
            power, curr_energy = get_power_watts(args.driver, hwmon_path, prev_energy, time_delta)
            
            mode = "REAL"
            if power is None:
                power = 10.0 + (util * 0.5) # Fallback Simulation
                mode = "SIM"

            # score = Throughput / Power
            throughput = float(open(f"{LOG_DIR}/{args.host_name}_throughput.txt").read().strip() or 0) if os.path.exists(f"{LOG_DIR}/{args.host_name}_throughput.txt") else 0.0
            score = throughput / power if power > 0 else 0.0

            # Log and Send Telemetry
            with open(csv_file, 'a', newline='') as f:
                csv.writer(f).writerow([curr_time, args.host_name, f"{util:.2f}", f"{throughput:.2f}", f"{power:.2f}", f"{score:.4f}"])
            
            sock.sendto(f"{args.host_name},{score:.4f},{util:.2f}".encode(), (SWITCH_IP, PORT))
            logging.info(f"[{mode}] Driver: {args.driver} | Host: {args.host_name} | Score: {score:.3f} | Pwr: {power:.1f}W")
            
            # Update states
            prev_idle, prev_total, prev_energy, prev_time = curr_idle, curr_total, curr_energy, curr_time

    except KeyboardInterrupt:
        sock.close()

if __name__ == "__main__":
    main()