import socket
import time
import argparse
import os
import threading
import csv

# --- LOGGING SETUP ---
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

def cpu_burner(duration_ms):
    end_time = time.time() + (duration_ms / 1000.0)
    while time.time() < end_time:
        _ = 1 * 1

def handle_request(sock, addr, identity, work_ms, csv_file):
    start_ts = time.time()
    try:
        # 1. Simulate Work
        if work_ms > 0:
            cpu_burner(work_ms)
        
        # 2. Send Reply
        reply = f"Reply from {identity}".encode()
        sock.sendto(reply, addr)
        
        # 3. Log Success
        end_ts = time.time()
        duration_ms = (end_ts - start_ts) * 1000
        
        # Append to CSV (Thread-safe enough for simple labs, use lock for production)
        with open(csv_file, 'a', newline='') as f:
            csv.writer(f).writerow([start_ts, addr[1], duration_ms])
            
    except Exception as e:
        print(f"Error: {e}")

def run_server(port, work_ms, server_id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    
    identity = server_id if server_id else os.uname()[1]
    
    # Init Log File
    csv_file = f"{LOG_DIR}/{identity}_work.csv"
    with open(csv_file, 'w', newline='') as f:
        csv.writer(f).writerow(["timestamp", "client_port", "processing_ms"])

    print(f"--- Server {identity} Listening on Port {port} ---")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            t = threading.Thread(target=handle_request, args=(sock, addr, identity, work_ms, csv_file), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--work", type=int, default=20)
    parser.add_argument("--id", type=str, default=None)
    args = parser.parse_args()

    run_server(args.port, args.work, args.id)