import time
import socket
import os
import sys
import logging

# --- Configuration ---
SWITCH_IP = "127.0.0.1"  # IP of the Controller listening for UDP
PORT = 50001
INTERVAL = 1.0           # Seconds between updates
BENCHMARK_SCORE = 100    # Capability constant (e.g., max ops/sec)
LOG_LEVEL = logging.INFO

logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s [%(levelname)s] %(message)s')

def get_cpu_utilization():
    """
    Reads /proc/stat directly. This is much faster than spawning a 'top' process
    and much more accurate for high-frequency reporting.
    """
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = list(map(int, line.split()[1:]))

        idle_time = parts[3]
        total_time = sum(parts)
        return idle_time, total_time
    except Exception as e:
        logging.error(f"Could not read CPU stats: {e}")
        return 0, 0

def get_energy_joules():
    """Reads Intel RAPL energy counter."""
    try:
        # Path might be intel-rapl:0 (Package) or intel-rapl:0:0 (Core)
        with open("/sys/class/powercap/intel-rapl:0/energy_uj", "r") as f:
            return int(f.read()) / 1000000.0 
    except FileNotFoundError:
        return None

def calculate_energy_efficiency(util, power):
    """Algorithm 1: Efficiency = Performance / Power."""
    # Prevent division by zero
    safe_power = max(power, 0.001) 
    # Even at 0% util, there is a baseline performance value in the paper's logic
    score = (max(util, 1.0) * BENCHMARK_SCORE) / safe_power
    return score

def main():
    if len(sys.argv) > 1:
        hostname = sys.argv[1]
    else:
        hostname = socket.gethostname() # Fallback

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # Initial readings for Delta calculations
    prev_idle, prev_total = get_cpu_utilization()
    prev_energy = get_energy_joules()
    prev_time = time.time()

    logging.info(f"MyLBName Agent started on {hostname}. Reporting to {SWITCH_IP}:{PORT}")

    try:
        while True:
            time.sleep(INTERVAL)
            
            # 1. Calculate CPU Util via Delta
            curr_idle, curr_total = get_cpu_utilization()
            diff_idle = curr_idle - prev_idle
            diff_total = curr_total - prev_total
            # Handle potential wrap-around or fresh boot edge cases
            util = 100.0 * (1.0 - (diff_idle / max(diff_total, 1)))
            
            # 2. Calculate Power Draw (Watts)
            curr_energy = get_energy_joules()
            curr_time = time.time()
            time_delta = curr_time - prev_time
            
            if curr_energy is not None and prev_energy is not None:
                # Energy counter resets are handled by the max(0, ...) check
                energy_delta = max(0, curr_energy - prev_energy)
                power_draw = energy_delta / time_delta
                mode = "REAL"
            else:
                # Simulation Fallback: Base 10W + 0.5W per % utilization
                power_draw = 10.0 + (util * 0.5)
                mode = "SIM"

            # 3. Calculate Efficiency Score
            score = calculate_energy_efficiency(util, power_draw)

            # 4. Telemetry
            message = f"{hostname},{score:.4f},{util:.2f}"
            try:
                sock.sendto(message.encode(), (SWITCH_IP, PORT))
                logging.info(f"[{mode}] {message} | Power: {power_draw:.2f}W")
            except Exception as e:
                logging.error(f"Failed to send telemetry: {e}")

            # 5. Update state for next iteration
            prev_idle, prev_total = curr_idle, curr_total
            prev_energy = curr_energy
            prev_time = curr_time

    except KeyboardInterrupt:
        logging.info("Agent shutting down.")
        sock.close()

if __name__ == "__main__":
    main()