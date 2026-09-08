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

def plot_host_scatter(df, host_label, cpu_col, power_col, output_filename, y_limits=None, x_limits=None):
    """
    Generates a scatter plot of every single raw Power Consumption vs. Host Load point.
    """
    fig, ax = plt.subplots(figsize=(10, 6))

    color_power = 'tab:blue'
    ax.set_xlabel('Host Load (CPU Utilization %)', fontsize=12)
    ax.set_ylabel('Power Consumption (Watts)', color=color_power, fontsize=12)
    
    # Apply global limits if provided to keep H2 and H3 scales identical
    if y_limits:
        ax.set_ylim(y_limits)
    if x_limits:
        ax.set_xlim(x_limits)
        
    # Use scatter instead of plot to avoid a massive spiderweb of lines.
    # Alpha=0.5 makes overlapping points darker, showing the actual density of CPU states.
    ax.scatter(df[cpu_col], df[power_col], color=color_power, alpha=0.5, edgecolors='none', label=f'{host_label} Raw Telemetry')
    
    ax.tick_params(axis='y', labelcolor=color_power)
    ax.grid(True, linestyle='--', alpha=0.6)

    ax.legend(loc='upper left')
    plt.title(f'{host_label} - Raw Power Consumption vs Host Load', fontsize=14)
    plt.tight_layout()
    plt.savefig(output_filename)
    print(f"SUCCESS: Graph saved to {output_filename}")
    plt.close()


def plot_final():
    base_dir = "paper/hardware_sift/roundrobin"
    
    print("Loading Raw Energy Logs...")
    h2_csv = os.path.join(base_dir, "h2_energy.csv")
    h3_csv = os.path.join(base_dir, "h3_energy.csv")
    
    try:
        df_h2 = pd.read_csv(h2_csv)
        df_h3 = pd.read_csv(h3_csv)
    except FileNotFoundError as e:
        print(f"Error loading files: {e}")
        return

    # Ensure the columns actually exist in your CSVs
    for col in ["cpu_util", "power_watts"]:
        if col not in df_h2.columns or col not in df_h3.columns:
            print(f"Error: Missing column '{col}' in one of the CSVs.")
            return

    # Calculate global min/max for uniform axes across both host plots
    global_y_min = min(df_h2["power_watts"].min(), df_h3["power_watts"].min())
    global_y_max = max(df_h2["power_watts"].max(), df_h3["power_watts"].max())
    y_padding = (global_y_max - global_y_min) * 0.05
    y_limits = (max(0, global_y_min - y_padding), global_y_max + y_padding)

    global_x_min = min(df_h2["cpu_util"].min(), df_h3["cpu_util"].min())
    global_x_max = max(df_h2["cpu_util"].max(), df_h3["cpu_util"].max())
    x_padding = (global_x_max - global_x_min) * 0.05
    x_limits = (max(0, global_x_min - x_padding), global_x_max + x_padding)

    # Generate Image 1: Host 2
    plot_host_scatter(df_h2, host_label="Host 2 (H2)", cpu_col="cpu_util", power_col="power_watts", 
              output_filename="h2_raw_power_vs_load.pdf", y_limits=y_limits, x_limits=x_limits)
    
    # Generate Image 2: Host 3
    plot_host_scatter(df_h3, host_label="Host 3 (H3)", cpu_col="cpu_util", power_col="power_watts", 
              output_filename="h3_raw_power_vs_load.pdf", y_limits=y_limits, x_limits=x_limits)


if __name__ == "__main__":
    plot_final()