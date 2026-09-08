import socket
import sys

VIP = "10.0.0.1"
PORT = 11211
TOTAL_KEYS = 100000
PAYLOAD_SIZE = 512 

print(f"Connecting to VIP {VIP}:{PORT} via TCP...")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((VIP, PORT))
except Exception as e:
    print(f"Connection failed: {e}")
    sys.exit(1)

payload = "A" * PAYLOAD_SIZE 
print(f"Pre-loading {TOTAL_KEYS} keys (key_0 to key_99999)...")

for i in range(TOTAL_KEYS):
    cmd = f"set key_{i} 0 0 {len(payload)}\r\n{payload}\r\n"
    s.sendall(cmd.encode())
    
    if i % 10000 == 0 and i > 0:
        s.recv(4096)
        print(f"  ... inserted {i} keys")

print("Pre-load complete! The backend servers are ready.")
s.close()