import socket
import time
import argparse

def run_client(target_ip, target_port, rate_pps, duration):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5) # 500ms timeout
    
    delay = 1.0 / rate_pps
    count = 0
    responses = {}

    print(f"--- Sending to VIP {target_ip}:{target_port} ---")
    print(f"--- Rate: {rate_pps} req/s | Duration: {duration}s ---")

    start_time = time.time()
    
    while time.time() - start_time < duration:
        try:
            # Send Request
            msg = f"Ping {count}".encode()
            sock.sendto(msg, (target_ip, target_port))
            count += 1
            
            # Receive Reply
            data, _ = sock.recvfrom(1024)
            reply = data.decode().strip()
            
            # Tally who answered
            server_name = reply.replace("Reply from ", "")
            responses[server_name] = responses.get(server_name, 0) + 1
            
            print(f"Seq {count}: {reply}")

        except socket.timeout:
            print(f"Seq {count}: TIMEOUT (Packet Lost)")
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(delay)

    print("\n--- Test Summary ---")
    print(f"Total Sent: {count}")
    for srv, hits in responses.items():
        print(f"  {srv}: {hits} responses ({100*hits/count:.1f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ip", help="Target VIP (e.g., 10.0.0.1)")
    parser.add_argument("--rate", type=int, default=10, help="Requests per second")
    parser.add_argument("--time", type=int, default=30, help="Test duration in seconds")
    args = parser.parse_args()

    run_client(args.ip, 8080, args.rate, args.time)