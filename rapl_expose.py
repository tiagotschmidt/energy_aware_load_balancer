import time
import os
import sys

# Path to the shared folder visible to the VM
# UPDATE THIS PATH to match your actual shared folder location
SHARED_FILE_PATH = "/home/ximit/Documents/energy_aware_load_balancer/rapl/rapl_value.txt"

def main():
    print(f"--- Starting RAPL Bridge ---")
    print(f"Reading from: /sys/class/powercap/intel-rapl:0/energy_uj")
    print(f"Writing to:   {SHARED_FILE_PATH}")

    try:
        while True:
            # 1. Read Hardware Sensor (Requires Root)
            with open("/sys/class/powercap/intel-rapl:0/energy_uj", "r") as f:
                energy_uj = f.read().strip()
            
            # 2. Write to Shared File
            # We overwrite the file instantly so the VM always sees the latest value
            with open(SHARED_FILE_PATH, "w") as f:
                f.write(energy_uj)
            
            # Update frequency (10Hz is enough for good granularity)
            time.sleep(0.001)
            
    except PermissionError:
        print("ERROR: You must run this script with sudo to read RAPL values.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()