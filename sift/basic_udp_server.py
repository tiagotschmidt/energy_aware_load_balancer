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

if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

print(f"--- Loading SIFT Data ({MAX_VECTORS} vectors)... ---")
try:
    full_data = np.load(DATA_FILE)
    database_vectors = full_data[:MAX_VECTORS].astype(np.float32)
    print(f"--- DB Ready: {database_vectors.shape} ---")
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

def handle_request(sock, addr, identity, csv_file, data):
    start_ts = time.time()
    try:
        query_vector = np.frombuffer(data, dtype=np.float32)
        
        # Verify dimension (SIFT is 128)
        if query_vector.shape[0] != 128: return

        # EXECUTE SEARCH
        result_idx = vector_search_cpu(query_vector)
        
        # Reply
        reply = f"Reply from {identity}: Match {result_idx}".encode()
        sock.sendto(reply, addr)
        
        # Log
        duration_ms = (time.time() - start_ts) * 1000
        with open(csv_file, 'a', newline='') as f:
            csv.writer(f).writerow([start_ts, addr[1], duration_ms])
            
    except Exception as e:
        print(f"Error processing request: {e}")

def run_server(port, server_id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    
    identity = server_id if server_id else "server_x"
    csv_file = f"{LOG_DIR}/{identity}_work.csv"
    
    # Init Log
    with open(csv_file, 'w', newline='') as f:
        csv.writer(f).writerow(["timestamp", "client_port", "processing_ms"])

    print(f"--- SIFT Server {identity} Listening on {port} ---")

    while True:
        try:
            # CLEAN LOOP: Receive data once, then process
            data, addr = sock.recvfrom(2048) 
            
            # Use Threading to allow CPU to burn without blocking the receive loop
            t = threading.Thread(target=handle_request, args=(sock, addr, identity, csv_file, data), daemon=True)
            t.start()
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Server Loop Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--id", type=str, default="h2")
    args = parser.parse_args()
    run_server(args.port, args.id)