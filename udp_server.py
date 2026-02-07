import socket
import time
import argparse
import os

def cpu_burner(duration_ms):
    """Spins the CPU to simulate processing load."""
    end_time = time.time() + (duration_ms / 1000.0)
    while time.time() < end_time:
        _ = 1 * 1  # Busy loop

def run_server(port, work_ms, server_id):
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    
    # Use provided ID or fallback to hostname
    identity = server_id if server_id else os.uname()[1]

    print(f"--- Server {identity} Listening on Port {port} ---")
    print(f"--- Simulation: {work_ms}ms CPU burn per request ---")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            
            # 1. Simulate Work (Burn CPU)
            if work_ms > 0:
                cpu_burner(work_ms)
            
            # 2. Send Reply with Identity
            reply = f"Reply from {identity}".encode()
            sock.sendto(reply, addr)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080, help="UDP Port")
    parser.add_argument("--work", type=int, default=20, help="CPU Burn time in ms")
    parser.add_argument("--id", type=str, default=None, help="Server Identifier (e.g. h2)")
    args = parser.parse_args()

    run_server(args.port, args.work, args.id)