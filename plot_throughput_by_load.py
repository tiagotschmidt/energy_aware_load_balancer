# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
# ]
# ///

import os
import pandas as pd
import matplotlib.pyplot as plt

def load_and_process(client_csv, h2_csv, h3_csv):
    """
    Reads logs and aggregates them by target_rate.
    Extracts average throughput and average CPU utilization for each load phase.
    """
    try:
        df_c = pd.read_csv(client_csv)
        df_h2 = pd.read_csv(h2_csv)
        df_h3 = pd.read_csv(h3_csv)
    except FileNotFoundError as e:
        print(f"Error loading files: {e}")
        return None

    if "target_rate" not in df_c.columns:
        print(f"Error: 'target_rate' column missing in {client_csv}")
        return None

    # Step 1: Find the time windows for each RPS phase
    phases = []
    for rps, group in df_c.groupby("target_rate"):
        start_t = group["timestamp"].min()
        end_t = group["timestamp"].max()
        phases.append((start_t, end_t, rps))
    
    # Step 2: Sort phases chronologically
    phases.sort(key=lambda x: x[0])

    results = []
    
    # Step 3: Extract throughput and CPU data for each phase
    for step_idx, (start_t, end_t, rps) in enumerate(phases):
        duration = end_t - start_t
        if duration <= 0:
            continue

        h2_step = df_h2[(df_h2["timestamp"] >= start_t) & (df_h2["timestamp"] <= end_t)]
        h3_step = df_h3[(df_h3["timestamp"] >= start_t) & (df_h3["timestamp"] <= end_t)]

        avg_tput_h2 = h2_step["throughput_rps"].mean() if not h2_step.empty and "throughput_rps" in h2_step.columns else 0
        avg_tput_h3 = h3_step["throughput_rps"].mean() if not h3_step.empty and "throughput_rps" in h3_step.columns else 0
        
        avg_cpu_h2 = h2_step["cpu_util"].mean() if not h2_step.empty and "cpu_util" in h2_step.columns else 0
        avg_cpu_h3 = h3_step["cpu_util"].mean() if not h3_step.empty and "cpu_util" in h3_step.columns else 0

        results.append({
            "target_rps": rps,
            "h2_tput": avg_tput_h2,
            "h3_tput": avg_tput_h3,
            "h2_cpu": avg_cpu_h2,
            "h3_cpu": avg_cpu_h3,
        })

    return pd.DataFrame(results)


def plot_host_throughput(df, host_label, cpu_col, tput_col, output_filename, y_limits=None, x_limits=None):
    """
    Generates a scatter/line plot of Throughput vs. Host Load.
    """
    # Sort by CPU load to ensure connecting lines follow logical progression
    df_sorted = df.sort_values(by=cpu_col)

    fig, ax = plt.subplots(figsize=(10, 6))

    color_tput = 'tab:purple'
    ax.set_xlabel('Host Load (CPU Utilization %)', fontsize=12)
    ax.set_ylabel('Throughput (RPS)', color=color_tput, fontsize=12)
    
    # Apply global limits if provided to keep H2 and H3 scales identical
    if y_limits:
        ax.set_ylim(y_limits)
    if x_limits:
        ax.set_xlim(x_limits)
        
    ax.plot(df_sorted[cpu_col], df_sorted[tput_col], marker='o', color=color_tput, linewidth=2.5, label=f'{host_label} Throughput')
    ax.tick_params(axis='y', labelcolor=color_tput)
    ax.grid(True, linestyle='--', alpha=0.6)

    # # Annotate points with the Target RPS value for extra context
    # for _, row in df_sorted.iterrows():
    #     ax.annotate(f"Target: {int(row['target_rps'])} RPS", 
    #                 (row[cpu_col], row[tput_col]),
    #                 textcoords="offset points", 
    #                 xytext=(0, 10), 
    #                 ha='center', fontsize=9)

    ax.legend(loc='upper left')
    plt.title(f'{host_label} - Throughput vs Host Load', fontsize=14)
    plt.tight_layout()
    plt.savefig(output_filename)
    print(f"SUCCESS: Graph saved to {output_filename}")
    plt.close()


def plot_final():
    base_dir = "data/leastu"
    
    print("Processing Logs for Throughput...")
    client_csv = os.path.join(base_dir, "client_sift_experiment.csv")
    h2_csv = os.path.join(base_dir, "h2_energy.csv")
    h3_csv = os.path.join(base_dir, "h3_energy.csv")
    
    df = load_and_process(client_csv, h2_csv, h3_csv)

    if df is None or df.empty:
        print("Skipping plot due to load errors or empty data. Check your file paths.")
        return

    # Calculate global min/max for uniform axes across both host plots
    global_y_min = min(df["h2_tput"].min(), df["h3_tput"].min())
    global_y_max = max(df["h2_tput"].max(), df["h3_tput"].max())
    y_padding = (global_y_max - global_y_min) * 0.05 if (global_y_max - global_y_min) > 0 else 10
    y_limits = (max(0, global_y_min - y_padding), global_y_max + y_padding)

    global_x_min = min(df["h2_cpu"].min(), df["h3_cpu"].min())
    global_x_max = max(df["h2_cpu"].max(), df["h3_cpu"].max())
    x_padding = (global_x_max - global_x_min) * 0.05 if (global_x_max - global_x_min) > 0 else 0.1
    x_limits = (max(0, global_x_min - x_padding), global_x_max + x_padding)

    # Generate Image 1: Host 2
    plot_host_throughput(df, host_label="Host 2 (H2)", cpu_col="h2_cpu", tput_col="h2_tput", 
                         output_filename="h2_throughput_vs_load.png", y_limits=y_limits, x_limits=x_limits)
    
    # Generate Image 2: Host 3
    plot_host_throughput(df, host_label="Host 3 (H3)", cpu_col="h3_cpu", tput_col="h3_tput", 
                         output_filename="h3_throughput_vs_load.png", y_limits=y_limits, x_limits=x_limits)


if __name__ == "__main__":
    plot_final()