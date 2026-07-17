import time
import socket
import os
import logging
import argparse
import csv
import json
import random

# --- Configuration ---
LOG_DIR = "sift/logs"
CONFIG_PATH = "simulation/config/host_profiles.json"
INTERVAL = 0.5

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

def load_host_profile(host_name):
    """Loads linear equation coefficients and pre-defined operator pools."""
    try:
        with open(CONFIG_PATH, "r") as f:
            profiles = json.load(f)
            # Default fallback if host isn't in JSON
            return profiles.get(host_name, {"m": 0.5, "c": 10.0, "pool": "decode"})
    except Exception as e:
        logging.warning(f"Could not load config ({e}). Using defaults.")
        return {"m": 0.5, "c": 10.0, "pool": "decode"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host_name", help="Name of this host")
    parser.add_argument("--controller-ip", default="127.0.0.1", help="Controller IP Address")
    parser.add_argument("--port", type=int, default=50001, help="Controller Port")
    args = parser.parse_args()

    # Load simulated hardware profile
    profile = load_host_profile(args.host_name)
    m = profile["m"]
    c = profile["c"]
    pool = profile["pool"]

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    csv_file = f"{LOG_DIR}/{args.host_name}_energy.csv"

    with open(csv_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp",
                "host",
                "pool",
                "cpu_util",
                "throughput_rps",
                "power_watts",
                "efficiency_score",
            ]
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    prev_time = time.time()
    
    logging.info(f"Started Sim Agent for {args.host_name} [Pool: {pool}] (m={m}, c={c})")

    try:
        while True:
            time.sleep(INTERVAL)
            curr_time = time.time()

            # Fetch Throughput
            try:
                with open(f"{LOG_DIR}/{args.host_name}_throughput.txt", "r") as f:
                    throughput_str = f.read().strip()
                    throughput = float(throughput_str) if throughput_str else 0.0
            except Exception:
                throughput = 0.0

            # Simulate Utilization based on throughput
            if throughput > 0:
                util = min(100.0, random.uniform(40.0, 95.0))
            else:
                util = random.uniform(0.5, 3.0) # Idle utilization

            # Apply Linear Energy Equation: E = m * x + c + noise
            noise = random.uniform(-1.5, 1.5)
            power = max(0.0, (m * util) + c + noise)

            # Calculate Efficiency Score
            score = 0.0
            if throughput == 0.0:
                score = -power   # Penalize zero throughput
            else:   
                score = throughput / power if power > 0 else 0.0

            # Log to CSV
            with open(csv_file, "a", newline="") as f:
                csv.writer(f).writerow(
                    [
                        curr_time,
                        args.host_name,
                        pool,
                        f"{util:.2f}",
                        f"{throughput:.2f}",
                        f"{power:.2f}",
                        f"{score:.4f}",
                    ]
                )

            # Send Telemetry to Controller
            sock.sendto(
                f"{args.host_name},{score:.4f},{util:.2f},{power:.2f}".encode(), 
                (args.controller_ip, args.port)
            )
            
            logging.info(
                f"[SIM] Host: {args.host_name} ({pool}) | Score: {score:.3f} | Pwr: {power:.1f}W | Util: {util:.1f}%"
            )

            prev_time = curr_time

    except KeyboardInterrupt:
        sock.close()

if __name__ == "__main__":
    main()