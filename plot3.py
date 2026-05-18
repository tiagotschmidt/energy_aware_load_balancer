import pandas as pd
import matplotlib.pyplot as plt


def load_and_process(mode_name, client_csv, h2_csv, h3_csv):
    """
    Reads logs and aggregates them by 'target_rate' (RPS Steps).
    Handles missing latency data for Open Loop tests.
    """
    # Load Data
    try:
        df_c = pd.read_csv(client_csv)
        df_h2 = pd.read_csv(h2_csv)
        df_h3 = pd.read_csv(h3_csv)
    except FileNotFoundError as e:
        print(f"Error loading {mode_name}: {e}")
        return None

    results = []

    # Group client data by the Step (Target RPS)
    if "target_rate" not in df_c.columns:
        print(f"Error: 'target_rate' column missing in {client_csv}")
        return None

    for rps, group in df_c.groupby("target_rate"):
        start_t = group["timestamp"].min()
        end_t = group["timestamp"].max()
        duration = end_t - start_t

        if duration <= 0:
            continue

        # 1. Latency (Handle Missing Data)
        if "latency_ms" in group.columns:
            success = group[group["status"] == "OK"]
            p99 = success["latency_ms"].quantile(0.99) if not success.empty else 0
        else:
            # Latency is undefined in Open Loop / Saturation tests
            p99 = 0

        # 2. Throughput (Real RPS vs Target RPS)
        # Count only successful replies
        success_count = len(group[group["status"] == "OK"])
        real_throughput = success_count / duration

        # 3. Power (Sum of H2 + H3 Avg Watts during this step)
        # Filter server logs to this specific time window
        h2_step = df_h2[(df_h2["timestamp"] >= start_t) & (df_h2["timestamp"] <= end_t)]
        h3_step = df_h3[(df_h3["timestamp"] >= start_t) & (df_h3["timestamp"] <= end_t)]

        avg_watts_h2 = h2_step["power_watts"].mean() if not h2_step.empty else 0
        avg_watts_h3 = h3_step["power_watts"].mean() if not h3_step.empty else 0

        total_cluster_power = avg_watts_h2 + avg_watts_h3

        results.append(
            {
                "rps": rps,
                "p99": p99,
                "throughput": real_throughput,
                "power": total_cluster_power,
            }
        )

    return pd.DataFrame(results)


def plot_final():
    # Load Data
    print("Processing Round Robin Logs...")
    df_perf = load_and_process(
        "Performance", "data/client_perf.csv", "data/h2_perf.csv", "data/h3_perf.csv"
    )

    print("Processing Energy Logs...")
    df_energy = load_and_process(
        "Energy-Aware",
        "data/client_energy.csv",
        "data/h2_energy.csv",
        "data/h3_energy.csv",
    )

    if df_perf is None or df_energy is None:
        print("Skipping plot due to load errors.")
        return

    # Create Plot (2 Rows if Latency is missing, 3 if present)
    has_latency = df_perf["p99"].sum() > 0
    rows = 3 if has_latency else 2

    fig, axes = plt.subplots(rows, 1, figsize=(8, 4 * rows), sharex=True)

    # Helper to handle 1D vs 2D array of axes
    if rows == 1:
        axes = [axes]

    plot_idx = 0

    # Plot 1: Latency (Only if data exists)
    if has_latency:
        axes[plot_idx].plot(
            df_perf["rps"], df_perf["p99"], "r--s", label="Round Robin Only"
        )
        axes[plot_idx].plot(
            df_energy["rps"], df_energy["p99"], "g-o", label="Energy Aware"
        )
        axes[plot_idx].set_ylabel("P99 Latency (ms)")
        axes[plot_idx].set_title("1. Latency (Lower is Better)")
        axes[plot_idx].legend()
        axes[plot_idx].grid(True)
        plot_idx += 1

    # Plot 2: Total Power
    axes[plot_idx].plot(
        df_perf["rps"], df_perf["power"], "r--s", label="Round Robin Only"
    )
    axes[plot_idx].plot(
        df_energy["rps"], df_energy["power"], "g-o", label="Energy Aware"
    )
    axes[plot_idx].set_ylabel("Cluster Power (Watts)")
    axes[plot_idx].set_title("Cluster Power Consumption (Lower is Better)")
    axes[plot_idx].legend()
    axes[plot_idx].grid(True)
    plot_idx += 1

    # Plot 3: Throughput
    axes[plot_idx].plot(
        df_perf["rps"], df_perf["throughput"], "r--s", label="Round Robin Only"
    )
    axes[plot_idx].plot(
        df_energy["rps"], df_energy["throughput"], "g-o", label="Energy Aware"
    )
    axes[plot_idx].plot(
        df_energy["rps"], df_energy["rps"], "k:", alpha=0.5, label="Ideal Target"
    )
    axes[plot_idx].set_ylabel("Throughput (Req/s)")
    axes[plot_idx].set_xlabel("Target Load (RPS)")
    axes[plot_idx].set_title("Throughput Capacity (Higher is Better)")
    axes[plot_idx].legend()
    axes[plot_idx].grid(True)

    plt.tight_layout()
    plt.savefig("final_saturation_comparison.png")
    print("\nSUCCESS: Graph saved to final_saturation_comparison.png")
    plt.show()


if __name__ == "__main__":
    plot_final()
