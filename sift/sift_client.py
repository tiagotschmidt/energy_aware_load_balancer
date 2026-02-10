import socket
import time
import argparse
import csv
import os
import numpy as np

LOG_DIR = "logs"
QUERY_FILE = "sift_data/queries.npy"

def run_client(target_ip, port, duration, rate):
    # Load Real Queries
    queries = np.load(QUERY_FILE).astype(np.float32)
    num_queries = queries.shape[0]
    print(f"--- Loaded {num_queries} SIFT queries ---")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    
    csv_file = f"{LOG_DIR}/client_sift_experiment.csv"
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "request_id", "status", "latency_ms", "server_id"])
    
    start_time = time.time()
    req_id = 0
    
    while time.time() - start_time < duration:
        loop_start = time.time()
        
        # Pick a query (Cyclic or Random)
        query_vec = queries[req_id % num_queries]
        
        try:
            # Send the raw bytes of the float32 vector (128 dims * 4 bytes = 512 bytes)
            sock.sendto(query_vec.tobytes(), (target_ip, port))
            
            send_ts = time.time()
            data, _ = sock.recvfrom(1024)
            recv_ts = time.time()
            
            latency = (recv_ts - send_ts) * 1000
            resp = data.decode()
            server_id = resp.split("Reply from ")[1].split(":")[0] if "Reply from" in resp else "unknown"
            
            with open(csv_file, 'a', newline='') as f:
                csv.writer(f).writerow([recv_ts, req_id, "OK", latency, server_id])
                
        except socket.timeout:
            with open(csv_file, 'a', newline='') as f:
                csv.writer(f).writerow([time.time(), req_id, "TIMEOUT", 0, "None"])
        except Exception as e:
            print(e)
            
        req_id += 1
        elapsed = time.time() - loop_start
        time.sleep(max(0, (1.0/rate) - elapsed))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", help="VIP Address")
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--time", type=int, default=30)
    args = parser.parse_args()
    
    run_client(args.ip, 8080, args.time, args.rate)