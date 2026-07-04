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
    Sorts them chronologically to represent the timeline of the experiment.
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
    
    # Step 2: Sort phases chronologically by their start time
    phases.sort(key=lambda x: x[0])

    results = []
    
    # Step 3: Extract power data for each chronological phase
    for step_idx, (start_t, end_t, rps) in enumerate(phases):
        duration = end_t - start_t
        if duration <= 0:
            continue

        h2_step = df_h2[(df_h2["timestamp"] >= start_t) & (df_h2["timestamp"] <= end_t)]
        h3_step = df_h3[(df_h3["timestamp"] >= start_t) & (df_h3["timestamp"] <= end_t)]

        avg_watts_h2 = h2_step["power_watts"].mean() if not h2_step.empty else 0
        avg_watts_h3 = h3_step["power_watts"].mean() if not h3_step.empty else 0

        results.append({
            "time_step": step_idx + 1,  # e.g., Phase 1, Phase 2, etc.
            "rps": rps,
            "h2_power": avg_watts_h2,
            "h3_power": avg_watts_h3,
        })

    return pd.DataFrame(results)


def plot_host(df, host_label, power_col, output_filename, y_limits=None):
    """
    Generates a single plot with a dual Y-axis for the specified host.
    """
    fig, ax1 = plt.subplots(figsize=(10, 6))

    # --- Primary Y-Axis: Power Consumption ---
    color_power = 'tab:blue'
    ax1.set_xlabel('Experiment Timeline (Steps)', fontsize=12)
    ax1.set_ylabel('Power Consumption (Watts)', color=color_power, fontsize=12)
    
    # Apply global limits if provided
    if y_limits:
        ax1.set_ylim(y_limits)
        
    line1 = ax1.plot(df["time_step"], df[power_col], marker='o', color=color_power, linewidth=2.5, label=f'{host_label} Power')
    ax1.tick_params(axis='y', labelcolor=color_power)
    ax1.grid(True, linestyle='--', alpha=0.6)

    # --- Secondary Y-Axis: Target RPS ---
    ax2 = ax1.twinx()  # Create a second y-axis that shares the same x-axis
    color_rps = 'tab:red'
    ax2.set_ylabel('Target Load (RPS)', color=color_rps, fontsize=12)
    line2 = ax2.plot(df["time_step"], df["rps"], marker='s', linestyle='--', color=color_rps, linewidth=2, label='Target Load (RPS)')
    ax2.tick_params(axis='y', labelcolor=color_rps)

    # --- Combine Legends ---
    lines = line1 + line2
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='upper left')

    plt.title(f'{host_label} - Power Consumption & Applied Load X Time', fontsize=14)
    plt.tight_layout()
    plt.savefig(output_filename)
    print(f"SUCCESS: Graph saved to {output_filename}")
    plt.close()  # Close figure to prevent overlap with the next plot


def plot_final():
    # Base directory according to your file tree
    base_dir = "data/leastu"
    
    print("Processing Energy Logs...")
    client_csv = os.path.join(base_dir, "client_sift_experiment.csv")
    h2_csv = os.path.join(base_dir, "h2_energy.csv")
    h3_csv = os.path.join(base_dir, "h3_energy.csv")
    
    df = load_and_process(client_csv, h2_csv, h3_csv)

    if df is None or df.empty:
        print("Skipping plot due to load errors or empty data. Check your file paths.")
        return

    # Calculate global min and max for the power columns to uniform the Y-axis
    global_min = min(df["h2_power"].min(), df["h3_power"].min())
    global_max = max(df["h2_power"].max(), df["h3_power"].max())
    
    # Add a 5% buffer top and bottom for visual clarity
    padding = (global_max - global_min) * 0.05
    y_limits = (max(0, global_min - padding), global_max + padding)

    # Generate Image 1: Host 2
    plot_host(df, host_label="Host 2 (H2)", power_col="h2_power", output_filename="h2_power_profile.png", y_limits=y_limits)
    
    # Generate Image 2: Host 3
    plot_host(df, host_label="Host 3 (H3)", power_col="h3_power", output_filename="h3_power_profile.png", y_limits=y_limits)


if __name__ == "__main__":
    plot_final()