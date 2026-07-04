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

def plot_efficiency_sample_to_sample(start_sec=0, end_sec=None):
    """
    Plots the sample-to-sample energy efficiency and target load.
    start_sec: The starting second for the visualization (default 0).
    end_sec: The ending second for the visualization (default to the end of the experiment).
    """
    # Base directory according to your file tree
    base_dir = "data/leastu"
    
    print(f"Processing Sample-to-Sample Energy Logs (Time window: {start_sec}s to {end_sec if end_sec else 'End'})...")
    client_csv = os.path.join(base_dir, "client_sift_experiment.csv")
    h2_csv = os.path.join(base_dir, "h2_energy.csv")
    h3_csv = os.path.join(base_dir, "h3_energy.csv")

    # Load data
    try:
        df_c = pd.read_csv(client_csv)
        df_h2 = pd.read_csv(h2_csv)
        df_h3 = pd.read_csv(h3_csv)
    except FileNotFoundError as e:
        print(f"Error loading files: {e}")
        return

    # Validate required columns
    if "target_rate" not in df_c.columns:
        print(f"Error: 'target_rate' column missing in {client_csv}")
        return
        
    if "efficiency_score" not in df_h2.columns or "efficiency_score" not in df_h3.columns:
        print("Error: 'efficiency_score' column missing in host logs.")
        return

    # Normalize timestamps so the X-axis starts at 0 (Time Elapsed)
    min_ts = min(df_c["timestamp"].min(), df_h2["timestamp"].min(), df_h3["timestamp"].min())
    
    df_c["time_rel"] = df_c["timestamp"] - min_ts
    df_h2["time_rel"] = df_h2["timestamp"] - min_ts
    df_h3["time_rel"] = df_h3["timestamp"] - min_ts

    # --- Apply Time Window Filtering ---
    if end_sec is None:
        end_sec = max(df_c["time_rel"].max(), df_h2["time_rel"].max(), df_h3["time_rel"].max())

    df_c = df_c[(df_c["time_rel"] >= start_sec) & (df_c["time_rel"] <= end_sec)]
    df_h2 = df_h2[(df_h2["time_rel"] >= start_sec) & (df_h2["time_rel"] <= end_sec)]
    df_h3 = df_h3[(df_h3["time_rel"] >= start_sec) & (df_h3["time_rel"] <= end_sec)]

    if df_h2.empty and df_h3.empty:
        print("Error: No data falls within the specified time window.")
        return

    # Create plot
    fig, ax1 = plt.subplots(figsize=(12, 6))

    # --- Primary Y-Axis: Energy Efficiency Score ---
    ax1.set_xlabel("Experiment Timeline (Seconds Elapsed)", fontsize=12)
    ax1.set_ylabel("Energy Efficiency Score", fontsize=12)
    
    # Plot H2 and H3 efficiency sample-to-sample
    line1 = ax1.plot(df_h2["time_rel"], df_h2["efficiency_score"], marker='.', markersize=4, linestyle='-', color='tab:blue', alpha=0.8, label="H2 Efficiency")
    line2 = ax1.plot(df_h3["time_rel"], df_h3["efficiency_score"], marker='.', markersize=4, linestyle='-', color='tab:green', alpha=0.8, label="H3 Efficiency")
    
    # Draw a clear horizontal baseline at 0 to easily spot negative values
    ax1.axhline(0, color='black', linewidth=1.5, linestyle='-', alpha=0.5)
    ax1.grid(True, linestyle='--', alpha=0.6)

    # --- Secondary Y-Axis: Target Load (RPS) ---
    ax2 = ax1.twinx()
    color_rps = 'tab:red'
    ax2.set_ylabel("Target Load (RPS)", color=color_rps, fontsize=12)
    
    line3 = ax2.plot(df_c["time_rel"], df_c["target_rate"], linestyle='--', color=color_rps, linewidth=2, label="Target Load (RPS)", drawstyle="steps-post")
    ax2.tick_params(axis='y', labelcolor=color_rps)

    # --- Combine Legends ---
    lines = line1 + line2 + line3
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='upper left')

    plt.title(f"Sample-to-Sample Controller Decisions ({start_sec}s - {end_sec:.1f}s)", fontsize=14)
    plt.tight_layout()
    
    # Format filename to include the timespan if provided
    suffix = f"_{start_sec}s_to_{int(end_sec)}s" if start_sec > 0 or end_sec != max(df_c["time_rel"].max(), df_h2["time_rel"].max(), df_h3["time_rel"].max()) else ""
    output_filename = f"efficiency_comparison_timeline{suffix}.png"
    
    plt.savefig(output_filename)
    print(f"SUCCESS: Graph saved to {output_filename}")
    plt.close()


if __name__ == "__main__":
    # Example 1: Full timeline (defaults)
    plot_efficiency_sample_to_sample()
    
    # Example 2: From 100th second to the end
    # plot_efficiency_sample_to_sample(start_sec=100)
    
    # Example 3: From 100th second to 200th second
    # plot_efficiency_sample_to_sample(start_sec=64,end_sec=66)
