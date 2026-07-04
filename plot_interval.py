import os
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

        # 2. Power (Sum of H2 + H3 Avg Watts during this step)
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
                "power": total_cluster_power,
            }
        )

    return pd.DataFrame(results)


def plot_final():
    # Base directory according to the provided tree
    base_dir = "data/agent_interval"
    
    # Configuration for the 3 deployments
    deployments = [
        {"label": "Interval 1.0s", "folder": "1sec", "style": "r--s"},
        {"label": "Interval 0.5s", "folder": "05sec", "style": "g-o"},
        {"label": "Interval 0.1s", "folder": "01sec", "style": "b-^"}
    ]

    processed_data = {}

    for dep in deployments:
        print(f"Processing {dep['label']} Logs...")
        
        # Build paths
        client_csv = os.path.join(base_dir, dep["folder"], "client_sift_experiment.csv")
        h2_csv = os.path.join(base_dir, dep["folder"], "h2_energy.csv")
        h3_csv = os.path.join(base_dir, dep["folder"], "h3_energy.csv")
        
        df = load_and_process(dep["label"], client_csv, h2_csv, h3_csv)
        
        if df is not None:
            processed_data[dep["label"]] = {
                "df": df,
                "style": dep["style"]
            }

    if not processed_data:
        print("Skipping plot due to load errors. Check your file paths.")
        return

    # Create Plot (2 Rows for Latency and Power)
    fig, axes = plt.subplots(2, 1, figsize=(8, 8), sharex=True)

    # --- Plot 1: Latency ---
    for label, data in processed_data.items():
        df = data["df"]
        axes[0].plot(df["rps"], df["p99"], data["style"], label=label)
        
    axes[0].set_ylabel("P99 Latency (ms)")
    axes[0].set_title("1. P99 Latency (Lower is Better)")
    axes[0].legend()
    axes[0].grid(True)

    # --- Plot 2: Total Power ---
    for label, data in processed_data.items():
        df = data["df"]
        axes[1].plot(df["rps"], df["power"], data["style"], label=label)
        
    axes[1].set_ylabel("Cluster Power (Watts)")
    axes[1].set_xlabel("Target Load (RPS)")
    axes[1].set_title("2. Cluster Power Consumption (Lower is Better)")
    axes[1].legend()
    axes[1].grid(True)

    plt.tight_layout()
    plt.savefig("interval_comparison.png")
    print("\nSUCCESS: Graph saved to interval_comparison.png")
    plt.show()

if __name__ == "__main__":
    plot_final()