import socket
import argparse
import os
import threading
import csv
import time
import numpy as np

LOG_DIR = "logs"
DATA_FILE = "sift_data/dataset.npy"
MAX_VECTORS = 100000 

# --- Global Metrics State ---
request_count = 0
request_lock = threading.Lock()

if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

print(f"--- Loading SIFT Data ({MAX_VECTORS} vectors)... ---")
try:
    # Ensure you have this file or the code will exit; 
    # for testing without data, you might want to create a dummy array.
    if os.path.exists(DATA_FILE):
        full_data = np.load(DATA_FILE)
        database_vectors = full_data[:MAX_VECTORS].astype(np.float32)
        print(f"--- DB Ready: {database_vectors.shape} ---")
    else:
        print("WARNING: Data file not found. Creating dummy data for test.")
        database_vectors = np.random.rand(MAX_VECTORS, 128).astype(np.float32)
except Exception as e:
    print(f"CRITICAL ERROR: Could not load dataset: {e}")
    exit(1)

def vector_search_cpu(query_vector):
    # 1. Subtract (Broadcasting)
    diff = database_vectors - query_vector
    # 2. Square and Sum (Einsum is faster)
    sq_dists = np.einsum('ij,ij->i', diff, diff)
    # 3. Partition to find top 1 
    nearest_idx = np.argpartition(sq_dists, 1)[0]
    return nearest_idx

def throughput_monitor(identity, interval=0.5):
    """
    Background thread that calculates requests/sec and writes to a file
    readable by the energy agent.
    """
    global request_count
    filename = f"{LOG_DIR}/{identity}_throughput.txt"
    
    print(f"--- Monitor started. Writing throughput to {filename} ---")
    
    while True:
        time.sleep(interval)
        
        with request_lock:
            # Calculate Rate (Requests per second)
            # If interval is 0.5s and we handled 10 reqs, throughput is 20 req/s
            current_throughput = request_count / interval
            request_count = 0 # Reset counter for next window
            
        # Atomic Write: Write to temp file first, then rename.
        # This prevents the Agent from reading an empty or partial file.
        temp_file = f"{filename}.tmp"
        try:
            with open(temp_file, "w") as f:
                f.write(f"{current_throughput:.2f}")
            os.replace(temp_file, filename)
        except Exception as e:
            print(f"Monitor Error: {e}")

def handle_request(sock, addr, identity, csv_file, data):
    global request_count
    start_ts = time.time()
    try:
        if len(data) < 512: return
        query_vector = np.frombuffer(data[:512], dtype=np.float32)
        
        # Parse Request ID
        try:
            req_id= data[515:].decode('utf-8')
        except:
            req_id = -1

        result_idx = vector_search_cpu(query_vector)
        
        reply = f"Reply from {identity} ID:{req_id} : Match {result_idx}".encode()
        sock.sendto(reply, addr)
        
        # Log work duration
        duration_ms = (time.time() - start_ts) * 1000
        with open(csv_file, 'a', newline='') as f:
            csv.writer(f).writerow([start_ts, addr[1], duration_ms])
        
        # Increment throughput counter
        with request_lock:
            request_count += 1
            
    except Exception as e:
        print(f"Error processing request: {e}")

def run_server(port, server_id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    
    identity = server_id if server_id else "server_x"
    csv_file = f"{LOG_DIR}/{identity}_work.csv"
    
    # Init Work Log
    with open(csv_file, 'w', newline='') as f:
        csv.writer(f).writerow(["timestamp", "client_port", "processing_ms"])

    # Start Throughput Monitor Thread
    t_mon = threading.Thread(target=throughput_monitor, args=(identity,), daemon=True)
    t_mon.start()

    print(f"--- SIFT Server {identity} Listening on {port} ---")

    while True:
        try:
            data, addr = sock.recvfrom(2048) 
            
            # Use Threading to allow CPU to burn without blocking
            t = threading.Thread(target=handle_request, args=(sock, addr, identity, csv_file, data), daemon=True)
            t.start()
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Server Loop Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080) # Default P4 tutorial port usually
    parser.add_argument("--id", type=str, default="h1")
    args = parser.parse_args()
    run_server(args.port, args.id)