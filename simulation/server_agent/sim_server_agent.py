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

# Saturation ceiling derived from simulation with 10 servers. The cluster's maximum throughput was 3400 (100% CPU reached at ~340 RPS)
SATURATION_RPS = 340.0

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


def load_host_profile(host_name):
    """Loads profile configuration. Baseline idle power c defaults to 4.8W."""
    try:
        with open(CONFIG_PATH, "r") as f:
            profiles = json.load(f)
            return profiles.get(host_name, {"m": 0.0636, "c": 4.80, "pool": "decode"})
    except Exception as e:
        logging.warning(f"Could not load config ({e}). Using default profile.")
        return {"m": 0.0636, "c": 4.80, "pool": "decode"}


def calculate_power_from_util(util, heterogenous_factor=1.0, idle_base=4.80):
    """Model 2: 4-Phase piecewise model mapping CPU Utilization (0-100%) to Watts."""
    """The model is based on real hardware readings (EPYC processor)"""
    if util <= 0.5:
        return idle_base
    elif util <= 20.0:
        # Phase 1: Wakeup / DVFS Ramp (+5.6W over 20% util)
        return idle_base + (util / 20.0) * 5.60 * heterogenous_factor
    elif util <= 50.0:
        # Phase 2: Moderate Scaling (+5.7W over 30% util)
        return (idle_base + 5.60) + ((util - 20.0) / 30.0) * 5.70 * heterogenous_factor
    elif util <= 88.0:
        # Phase 3: Efficiency Plateau (+1.5W over 38% util) -> The Sweet Spot!
        return (idle_base + 11.30) + ((util - 50.0) / 38.0) * 1.50 * heterogenous_factor
    else:
        # Phase 4: High-Load Saturation Spike (+7.8W over 12% util)
        overflow = min(12.0, util - 88.0)
        return (idle_base + 12.80) + (overflow / 12.0) * 7.80 * heterogenous_factor


def main():
    parser = argparse.ArgumentParser(description="Two-Step Pipeline Energy Sim Agent")
    parser.add_argument("host_name", help="Name of this host (e.g., h2)")
    parser.add_argument(
        "--controller-ip", default="127.0.0.1", help="Controller IP Address"
    )
    parser.add_argument("--port", type=int, default=50001, help="Controller Port")
    args = parser.parse_args()

    profile = load_host_profile(args.host_name)
    idle_base = float(profile["c"])
    heterogenous_factor = float(profile["m"])
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

    logging.info(
        f"Started Sim Agent for {args.host_name} [Pool: {pool}] (Idle Base={idle_base:.2f}W) (Heterogeneous Factor={profile['m']:.4f})"
    )

    try:
        while True:
            time.sleep(INTERVAL)
            curr_time = time.time()

            # --- Read Real-Time Throughput ---
            try:
                with open(f"{LOG_DIR}/{args.host_name}_throughput.txt", "r") as f:
                    throughput_str = f.read().strip()
                    throughput = float(throughput_str) if throughput_str else 0.0
            except Exception:
                throughput = 0.0

            # --- MODEL Throughput -> CPU Util ---
            if throughput > 0:
                base_util = (0.2897 * throughput) + 1.5
                util_noise = random.gauss(0.0, 2.0)
                util = max(0.5, min(100.0, base_util + util_noise))
            else:
                util = random.uniform(0.0, 3.0)

            # --- MODEL CPU Util -> Power (4-Phase Model) ---
            if throughput > 0:
                base_power = calculate_power_from_util(
                    util, heterogenous_factor, idle_base
                )
                pwr_noise = random.uniform(-0.3, 0.3)
                power = max(idle_base, min(26.0, base_power + pwr_noise))
            else:
                power = max(3.8, idle_base + random.uniform(-0.2, 0.2))

            # --- Calculate Efficiency Score (RPS per Watt) ---
            if throughput == 0.0:
                score = -power  # Penalize idle power drain
            else:
                score = throughput / power if power > 0 else 0.0

            # --- Log & Transmit Telemetry ---
            with open(csv_file, "a", newline="") as f:
                csv.writer(f).writerow(
                    [
                        f"{curr_time:.6f}",
                        args.host_name,
                        pool,
                        f"{util:.2f}",
                        f"{throughput:.2f}",
                        f"{power:.2f}",
                        f"{score:.4f}",
                    ]
                )

            payload = f"{args.host_name},{score:.4f},{util:.2f},{power:.2f}"
            sock.sendto(payload.encode(), (args.controller_ip, args.port))

            logging.info(
                f"[SIM] Host: {args.host_name:3} ({pool:7}) | RPS: {throughput:6.1f} | Util: {util:5.1f}% | Pwr: {power:5.1f}W | Score: {score:6.2f}"
            )

    except KeyboardInterrupt:
        logging.info("Agent shutting down cleanly.")
        sock.close()


if __name__ == "__main__":
    main()
