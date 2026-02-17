import socket
import time
import argparse
import csv
import os
import threading
import numpy as np
import queue

LOG_DIR = "logs"
QUERY_FILE = "sift_data/queries.npy"

# Shared Data
STOP_EVENT = threading.Event()
STATS_QUEUE = queue.Queue()
INFLIGHT_TIMESTAMPS = {} # Maps req_id -> send_time
LOCK = threading.Lock()

def receiver_thread(sock):
    """Listens for replies and calculates latency using the Echoed ID."""
    while not STOP_EVENT.is_set():
        try:
            data, _ = sock.recvfrom(1024)
            recv_ts = time.time()
            
            resp = data.decode(errors='ignore')
            
            if "Reply from" in resp and "ID:" in resp:
                parts = resp.split(" ")
                
                server_id = parts[2]
                
                id_part = next((s for s in parts if s.startswith("ID:")), None)
                
                latency = 0
                if id_part:
                    try:
                        req_id = int(id_part.split(":")[1])
                        
                        with LOCK:
                            if req_id in INFLIGHT_TIMESTAMPS:
                                send_ts = INFLIGHT_TIMESTAMPS.pop(req_id)
                                latency = (recv_ts - send_ts) * 1000
                    except:
                        pass
                
                STATS_QUEUE.put((recv_ts, "OK", server_id, latency))
            
        except socket.timeout:
            continue
        except Exception as e:
            continue

def run_open_loop_test(target_ip, port, min_rate, max_rate, step_size, step_duration):
    if not os.path.exists(QUERY_FILE):
        print("Error: Queries file not found.")
        return

    queries = np.load(QUERY_FILE).astype(np.float32)
    num_queries = queries.shape[0]

    csv_file = f"{LOG_DIR}/client_sift_experiment.csv"
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "status", "server_id", "latency_ms", "target_rate"])

    # Create ONE persistent socket for both sending and receiving
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.1) 

    # The receiver thread now uses the SAME socket used for sending
    recv_thread = threading.Thread(target=receiver_thread, args=(sock,))
    recv_thread.start()

    print(f"--- STARTING SINGLE-SOCKET LOAD: {min_rate} -> {max_rate} RPS ---")
    
    current_rate = min_rate
    req_id = 0

    try:
        while current_rate <= max_rate:
            print(f">>> RAMPING UP: {current_rate} RPS")
            step_start = time.time()
            inter_packet_delay = 1.0 / current_rate
            
            while time.time() - step_start < step_duration:
                loop_start = time.time()
                
                query = queries[req_id % num_queries]
                query_bytes = query.tobytes()
                id_bytes = f"ID:{req_id}".encode('utf-8') # Added "ID:" prefix for easier parsing
                
                try:
                    # Track Timestamp before sending
                    with LOCK:
                        INFLIGHT_TIMESTAMPS[req_id] = time.time()
                    
                    # Use the persistent socket
                    sock.sendto(query_bytes + id_bytes, (target_ip, port))
                except Exception as e:
                    print(f"Send Error: {e}")

                req_id += 1
                
                # Precise timing for open-loop rate control
                elapsed = time.time() - loop_start
                sleep_time = max(0, inter_packet_delay - elapsed)
                time.sleep(sleep_time)

            # Process collected statistics for this step
            count = 0
            while not STATS_QUEUE.empty():
                ts, status, srv_id, lat = STATS_QUEUE.get()
                with open(csv_file, 'a', newline='') as f:
                    csv.writer(f).writerow([ts, status, srv_id, lat, current_rate])
                count += 1
            
            print(f"    Step Finished. Logged {count} replies at {current_rate} RPS.")
            current_rate += step_size

    finally:
        STOP_EVENT.set()
        recv_thread.join()
        sock.close()
        print("--- TEST FINISHED ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="10.0.0.1")
    parser.add_argument("--min", type=int, default=10)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--duration", type=int, default=10)
    args = parser.parse_args()
    
    run_open_loop_test(args.ip, 8080, args.min, args.max, args.step, args.duration)