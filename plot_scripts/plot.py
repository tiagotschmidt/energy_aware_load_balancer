import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import sys

# --- CONFIGURATION: PATHS MATCHING YOUR TREE ---
ENERGY_LOG_PATTERN = "server_agent/logs/*_energy.csv"
CLIENT_LOG_PATTERN = "sift/logs/client_sift_test.csv"  # Or experiment.csv
SERVER_WORK_PATTERN = "sift/logs/*_work.csv"


def load_latest_file(pattern):
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getctime)


def plot_dashboard():
    print("--- Generating Experiment Dashboard ---")

    # 1. Load Client Data (Latency & Decisions)
    client_file = load_latest_file("sift/logs/client*.csv")
    if not client_file:
        print("ERROR: No client logs found in sift/logs/")
        return

    print(f"Loading Client Log: {client_file}")
    df_client = pd.read_csv(client_file)

    # Normalize Time (Start at 0)
    start_time = df_client["timestamp"].min()
    df_client["rel_time"] = df_client["timestamp"] - start_time

    # 2. Load Energy Data (H2 and H3)
    energy_files = glob.glob(ENERGY_LOG_PATTERN)
    energy_dfs = {}

    for e_file in energy_files:
        host = os.path.basename(e_file).split("_")[0]  # 'h2' from 'h2_energy.csv'
        print(f"Loading Energy Log: {e_file}")
        df = pd.read_csv(e_file)
        # Align time with client start time
        df["rel_time"] = df["timestamp"] - start_time
        energy_dfs[host] = df

    # --- PLOTTING ---
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)

    # AX1: Latency (QoS)
    # Filter timeouts vs success
    success = df_client[df_client["status"] == "OK"]
    timeout = df_client[df_client["status"] == "TIMEOUT"]

    axes[0].scatter(
        success["rel_time"],
        success["latency_ms"],
        s=10,
        alpha=0.6,
        label="Latency (ms)",
        color="blue",
    )

    if not timeout.empty:
        axes[0].vlines(
            timeout["rel_time"],
            0,
            success["latency_ms"].max(),
            colors="red",
            alpha=0.5,
            label="TIMEOUT (Drop)",
        )

    axes[0].set_ylabel("Latency (ms)")
    axes[0].set_title("1. Application Performance (SIFT Vector Search)")
    axes[0].legend(loc="upper left")
    axes[0].grid(True, alpha=0.3)

    # AX2: Load Balancing Decisions
    # We want to see who handled the requests over time
    # Bin data by second to calculate Requests Per Second (RPS) per server
    df_client["time_bin"] = df_client["rel_time"].astype(int)

    # Pivot: Index=Time, Columns=Server, Values=Count
    lb_decisions = df_client.pivot_table(
        index="time_bin", columns="server_id", values="latency_ms", aggfunc="count"
    ).fillna(0)

    colors = {"h2": "tab:orange", "h3": "tab:green", "None": "red"}

    for col in lb_decisions.columns:
        if col in colors:
            axes[1].plot(
                lb_decisions.index,
                lb_decisions[col],
                label=f"{col} Load",
                color=colors[col],
                linewidth=2,
            )

    axes[1].set_ylabel("Throughput (Req/s)")
    axes[1].set_title("2. Load Balancer Distribution Strategy")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    # AX3: Power Consumption (The "Why")
    total_joules = 0
    for host, df in energy_dfs.items():
        # Smooth the noisy data slightly for plotting
        smooth_power = df["power_watts"].rolling(window=3).mean()
        color = "tab:orange" if host == "h2" else "tab:green"

        axes[2].plot(
            df["rel_time"], smooth_power, label=f"{host} Power (W)", color=color
        )
        axes[2].fill_between(df["rel_time"], smooth_power, alpha=0.1, color=color)

        # Simple Riemann Sum for Energy
        # (Assuming ~1s interval, or calculate strictly with time_delta)
        if "total_energy_joules" in df.columns:
            # If log has cumulative, take max - min
            joules = df["total_energy_joules"].max() - df["total_energy_joules"].min()
        else:
            # Fallback integration
            joules = df["power_watts"].sum()

        total_joules += joules

    axes[2].set_ylabel("Power (Watts)")
    axes[2].set_xlabel("Experiment Time (seconds)")
    axes[2].set_title(f"3. Energy Consumption (Total Est: {int(total_joules)} Joules)")
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("experiment_results.png")
    print("\nSUCCESS: Graph saved to 'experiment_results.png'")
    plt.show()


if __name__ == "__main__":
    plot_dashboard()
