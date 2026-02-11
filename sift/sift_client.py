import socket
import time
import argparse
import csv
import os
import numpy as np

LOG_DIR = "logs"
QUERY_FILE = "sift_data/queries.npy"

def run_step_test(target_ip, port, min_rate, max_rate, step_size, step_duration):
    # Load Real Queries
    if not os.path.exists(QUERY_FILE):
        print(f"ERROR: {QUERY_FILE} not found. Run prepare_sift.py")
        return

    queries = np.load(QUERY_FILE).astype(np.float32)
    num_queries = queries.shape[0]
    print(f"--- Loaded {num_queries} SIFT queries ---")
    print(f"--- Starting Step Test: {min_rate} -> {max_rate} RPS (Step: {step_size}, Duration: {step_duration}s) ---")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    
    csv_file = f"{LOG_DIR}/client_sift_experiment.csv"
    
    # NEW HEADER: Added 'target_rate' to group data later
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "request_id", "status", "latency_ms", "server_id", "target_rate"])
    
    req_id = 0
    current_rate = min_rate

    while current_rate <= max_rate:
        print(f">>> STEP: {current_rate} RPS for {step_duration}s")
        step_start_time = time.time()
        
        while time.time() - step_start_time < step_duration:
            loop_start = time.time()
            
            # Pick query
            query_vec = queries[req_id % num_queries]
            
            try:
                # Send
                send_ts = time.time()
                sock.sendto(query_vec.tobytes(), (target_ip, port))
                
                # Receive
                data, _ = sock.recvfrom(1024)
                recv_ts = time.time()
                latency = (recv_ts - send_ts) * 1000
                
                resp = data.decode()
                server_id = resp.split("Reply from ")[1].split(":")[0] if "Reply from" in resp else "unknown"
                status = "OK"
            
            except socket.timeout:
                recv_ts = time.time()
                latency = 0
                server_id = "None"
                status = "TIMEOUT"
            except Exception as e:
                print(e)
                continue

            # LOGGING: Include current_rate
            with open(csv_file, 'a', newline='') as f:
                csv.writer(f).writerow([recv_ts, req_id, status, latency, server_id, current_rate])

            req_id += 1
            
            # Precise Rate Limiting
            elapsed = time.time() - loop_start
            sleep_time = max(0, (1.0/current_rate) - elapsed)
            time.sleep(sleep_time)
        
        # Move to next step
        current_rate += step_size

    print("--- Test Complete ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", help="VIP Address", default="10.0.0.1")
    parser.add_argument("--min", type=int, default=10, help="Start RPS")
    parser.add_argument("--max", type=int, default=100, help="End RPS")
    parser.add_argument("--step", type=int, default=10, help="RPS Increase")
    parser.add_argument("--duration", type=int, default=10, help="Seconds per step")
    args = parser.parse_args()
    
    run_step_test(args.ip, 8080, args.min, args.max, args.step, args.duration)