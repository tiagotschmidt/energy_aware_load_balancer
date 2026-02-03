import time
import socket
import os

# Configuration
SWITCH_IP = "127.0.0.1"  # The Load Balancer's control IP
PORT = 50001
INTERVAL = 1.0  # Reporting interval in seconds
BENCHMARK_SCORE = 100  # Constant benchmark score for this hardware


def get_cpu_utilization():
    # In a real system, use psutil or read /proc/stat
    # For now, we'll return a dummy value or use top
    return float(
        os.popen("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'").read().replace(",", ".")
    )


def get_energy_joules():
    """
    Attempts to read Intel RAPL energy via sysfs.
    Path may vary: /sys/class/powercap/intel-rapl:0/energy_uj
    Returns 0.0 if not available (simulation mode).
    """
    try:
        with open("/sys/class/powercap/intel-rapl:0/energy_uj", "r") as f:
            return int(f.read()) / 1000000.0  # Convert microjoules to Joules
    except FileNotFoundError:
        return None


def main():
    prev_energy = get_energy_joules()
    previous_time = time.time()

    print("MyLBName Agent starting...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        time.sleep(INTERVAL)

        current_cpu_utilization = get_cpu_utilization()
        current_energy = get_energy_joules()
        current_time = time.time()

        # Calculate Power (Watts = Joules / Seconds)
        if current_energy is not None and prev_energy is not None:
            power_draw = (current_energy - prev_energy) / (current_time - previous_time)
        else:
            # Simulation fallback: Power scales with utilization
            power_draw = 10.0 + (current_cpu_utilization * 0.5)

        score = calculate_energy_efficiency(current_cpu_utilization, power_draw)

        message = f"{socket.gethostname()},{score:.4f},{current_cpu_utilization:.2f}"
        sock.sendto(message.encode(), (SWITCH_IP, PORT))

        print(f"Sent: {message} | Power: {power_draw:.2f}W")

        prev_energy = current_energy
        previous_time = current_time


def calculate_energy_efficiency(current_cpu_utilization, power_draw):
    if current_cpu_utilization > 0:
        score = (current_cpu_utilization * BENCHMARK_SCORE) / power_draw
    else:
        score = BENCHMARK_SCORE / power_draw
    return score


if __name__ == "__main__":
    main()
