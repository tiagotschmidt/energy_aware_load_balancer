import time
import socket
import os
import sys
import logging
import argparse
import random
import csv

# --- Configuration ---
SWITCH_IP = "127.0.0.1"  # IP of the Controller listening for UDP
PORT = 50001
INTERVAL = 1.0           # Seconds between updates
BENCHMARK_SCORE = 100    # Capability constant (e.g., max ops/sec)

#RAPL_FILE = "/sys/class/powercap/intel-rapl:0/energy_uj"
RAPL_FILE = "../rapl/rapl_value.txt"

# Log Setup (Console)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def get_cpu_utilization(cpu_core: str, prev_idle, prev_total):
    """
    Reads /proc/stat for a specific core (if pinned) or global usage.
    Returns: (util_percent, curr_idle, curr_total)
    """
    try:
        curr_idle = 0
        curr_total = 0
        found = False

        with open('/proc/stat', 'r') as f:
            for line in f:
                parts = line.split()
                if parts[0].startswith('cpu') and len(parts[0]) > 3:
                    core_id = parts[0][3:]
                    if core_id == cpu_core:
                        metrics = list(map(int, parts[1:]))
                        curr_idle = metrics[3] + metrics[4] # idle + iowait
                        curr_total = sum(metrics)
                        found = True
                        break
        
        if not found:
            # Fallback to global if core not found
             with open('/proc/stat', 'r') as f:
                line = f.readline() 
                metrics = list(map(int, line.split()[1:]))
                curr_idle = metrics[3] + metrics[4]
                curr_total = sum(metrics)

        diff_idle = curr_idle - prev_idle
        diff_total = curr_total - prev_total
        
        if diff_total == 0: return 0.0, curr_idle, curr_total

        util = 100.0 * (1.0 - (diff_idle / diff_total))
        return max(0.0, util), curr_idle, curr_total

    except Exception as e:
        logging.error(f"CPU Read Error: {e}")
        return 0.0, prev_idle, prev_total

def get_energy_joules():
    """Reads Intel RAPL energy counter (System Wide)."""
    try:
        with open( RAPL_FILE , "r") as f:
            return int(f.read()) / 1000000.0 
    except ValueError:
        return None
    except FileNotFoundError:
        return None

def calculate_energy_efficiency(util, power):
    """Algorithm: Efficiency = Performance / Power."""
    safe_power = max(power, 0.001) 
    score = (max(util, 1.0) * BENCHMARK_SCORE) / safe_power
    return score

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host_name", help="Name of this host (e.g., h2)")
    parser.add_argument("cpu_core", help="Pinned CPU Core ID (e.g., 0)")
    parser.add_argument("--efficiency", type=float, default=None, help="Optional: Override random efficiency")
    
    args = parser.parse_args()

    startup_efficiency = random.uniform(0.7, 1.3)

    # --- CSV LOGGING SETUP ---
    log_dir = "logs"
    if not os.path.exists(log_dir): os.makedirs(log_dir)
    
    csv_file = f"{log_dir}/{args.host_name}_energy.csv"
    
    # Initialize CSV with Headers
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "host", "cpu_util", "power_watts", "efficiency_score"])
    
    logging.info(f"Agent {args.host_name} Started.")
    logging.info(f"Hardware Profile Generated: Efficiency Factor = {startup_efficiency:.2f} (70%-130% Range)")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    _, prev_idle, prev_total = get_cpu_utilization(args.cpu_core, 0, 0)
    prev_energy = get_energy_joules()
    prev_time = time.time()
    
    try:
        while True:
            time.sleep(INTERVAL)
            
            # 1. Metrics
            util, curr_idle, curr_total = get_cpu_utilization(args.cpu_core, prev_idle, prev_total)
            curr_energy = get_energy_joules()
            curr_time = time.time()
            time_delta = curr_time - prev_time
            
            # 2. Power Calculation (Real or Sim)
            if curr_energy is not None and prev_energy is not None:
                energy_delta = max(0, curr_energy - prev_energy)
                raw_power = energy_delta / time_delta
                mode = "REAL"
            else:
                # Fallback Simulation
                raw_power = 10.0 + (util * 0.5)
                mode = "SIM"

            # 3. Apply The Startup Factor (Hardware Profile)
            # This factor stays constant for the life of the agent
            profiled_power = raw_power * startup_efficiency

            # 4. Calculate Score
            score = calculate_energy_efficiency(util, profiled_power)

            # --- 5. LOG TO CSV ---
            with open(csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([curr_time, args.host_name, f"{util:.2f}", f"{profiled_power:.2f}", f"{score:.2f}"])

            # 6. Send Telemetry
            message = f"{args.host_name},{score:.4f},{util:.2f}"
            try:
                sock.sendto(message.encode(), (SWITCH_IP, PORT))
                logging.info(f"[{mode}] Sent: {message} | Pwr: {profiled_power:.2f}W (Factor: {startup_efficiency:.2f})")
            except Exception as e:
                logging.error(f"UDP Error: {e}")

            # 7. Update State
            prev_idle, prev_total = curr_idle, curr_total
            prev_energy = curr_energy
            prev_time = curr_time

    except KeyboardInterrupt:
        logging.info("Agent shutting down.")
        sock.close()

if __name__ == "__main__":
    main()