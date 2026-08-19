# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""
Controller Scalability Data Parsing and Plotting Script 
==========================================================================
Description:
    Parses pidstat logs (`pidstat -r -u 1`) from Load Balancer controller
    scalability experiments (M = 2, 4, 8, 16, 32 nodes) and generates a 
    publication-ready dual-axis line plot for CPU utilization (%) and 
    Memory footprint (MB).

Usage:
    uv run plot_controller_scalability.py
    # or:
    python3 plot_controller_scalability.py

Options:
    --log-dir DIR        Directory containing log files (default: logs/)
    --m-values M [M ...] Cluster sizes M to process (default: 2 4 8 16 32)
    --output-pdf FILE    Path for output PDF figure (default: controller_scalability.pdf)
    --output-png FILE    Path for output PNG figure (default: controller_scalability.png)
    --error-bars         Include standard deviation error bars
    --generate-mock      Generate synthetic logs for missing M files for dry-run testing
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def parse_pidstat_file(filepath: str) -> dict:
    """
    Parses a single pidstat -r -u 1 log file.
    
    Extracts:
      - %usr (CPU user-space utilization percentage)
      - RSS  (Resident Set Size in KB -> converted to MB)
      
    Returns a dictionary of summary statistics (mean, std, min, max, sample count).
    """
    if not os.path.exists(filepath):
        return None

    cpu_usr_values = []
    rss_mb_values = []

    current_section = None
    usr_col_idx = None
    rss_col_idx = None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line_str = line.strip()
            # Skip empty lines, system banner headers, and summary averages
            if not line_str or line_str.startswith("Linux") or line_str.startswith("Average:"):
                continue

            tokens = line_str.split()

            # Detect CPU header
            if "%usr" in tokens:
                current_section = "CPU"
                usr_col_idx = tokens.index("%usr")
                continue
            # Detect Memory header
            elif "RSS" in tokens:
                current_section = "MEM"
                rss_col_idx = tokens.index("RSS")
                continue

            # Parse CPU sample line
            if current_section == "CPU" and usr_col_idx is not None:
                try:
                    val = float(tokens[usr_col_idx])
                    cpu_usr_values.append(val)
                except (ValueError, IndexError):
                    pass
            # Parse Memory sample line
            elif current_section == "MEM" and rss_col_idx is not None:
                try:
                    val_kb = float(tokens[rss_col_idx])
                    val_mb = val_kb / 1024.0  # Convert KB to MB
                    rss_mb_values.append(val_mb)
                except (ValueError, IndexError):
                    pass

    if not cpu_usr_values or not rss_mb_values:
        print(f"[WARN] Incomplete data in {filepath} (CPU samples: {len(cpu_usr_values)}, MEM samples: {len(rss_mb_values)})")
        return None

    cpu_arr = np.array(cpu_usr_values)
    mem_arr = np.array(rss_mb_values)

    return {
        "cpu_usr_mean": float(np.mean(cpu_arr)),
        "cpu_usr_std": float(np.std(cpu_arr)),
        "cpu_usr_min": float(np.min(cpu_arr)),
        "cpu_usr_max": float(np.max(cpu_arr)),
        "rss_mb_mean": float(np.mean(mem_arr)),
        "rss_mb_std": float(np.std(mem_arr)),
        "rss_mb_min": float(np.min(mem_arr)),
        "rss_mb_max": float(np.max(mem_arr)),
        "sample_count_cpu": len(cpu_arr),
        "sample_count_mem": len(mem_arr),
    }


def load_scalability_data(log_dir: str, m_values: list) -> pd.DataFrame:
    """
    Loads all log files for the specified cluster sizes M from log_dir.
    Returns a pandas DataFrame sorted by M.
    """
    records = []

    for m in m_values:
        filename = f"controller_scale_M{m}.log"
        filepath = os.path.join(log_dir, filename)
        
        print(f"[*] Reading log for M={m:2d}: {filepath} ... ", end="")
        stats = parse_pidstat_file(filepath)
        
        if stats is not None:
            stats["M"] = m
            stats["filename"] = filename
            records.append(stats)
            print(f"OK ({stats['sample_count_cpu']} samples | CPU: {stats['cpu_usr_mean']:.2f}% | Mem: {stats['rss_mb_mean']:.2f} MB)")
        else:
            print("NOT FOUND or EMPTY")

    if not records:
        print(f"[ERROR] No valid log files found in '{log_dir}'.")
        return pd.DataFrame()

    df = pd.DataFrame(records).sort_values("M").reset_index(drop=True)
    return df


def plot_dual_axis_scalability(
    df: pd.DataFrame,
    output_pdf: str = "controller_scalability.pdf",
    output_png: str = "controller_scalability.png",
    show_error_bars: bool = False
):
    """
    Generates an academic publication-ready dual-axis plot.
    
    Primary Y-Axis (Left):  Controller CPU Utilization (%) - Solid blue line with circle markers
    Secondary Y-Axis (Right): Controller Memory (MB) - Dashed orange line with square markers
    X-Axis: Categorical ticks for M = [2, 4, 8, 16, 32]
    """
    if df.empty:
        print("[ERROR] DataFrame is empty. Cannot generate plot.")
        return

    # Publication style configuration
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
        "axes.edgecolor": "#333333",
        "axes.linewidth": 1.0,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.size": 5,
        "ytick.major.size": 5,
    })

    fig, ax1 = plt.subplots(figsize=(7.0, 4.8), dpi=300)
    ax2 = ax1.twinx()

    # Color palette & styling
    cpu_color = "#1f77b4"     # IEEE publication blue
    mem_color = "#ff7f0e"     # High-contrast publication orange
    line_width = 2.4
    marker_size = 7.5

    # Categorical X positions
    m_labels = [str(m) for m in df["M"]]
    x_positions = np.arange(len(df["M"]))

    # 1. Primary Y-Axis (Left): CPU Utilization (%)
    if show_error_bars and "cpu_usr_std" in df.columns:
        line1 = ax1.errorbar(
            x_positions,
            df["cpu_usr_mean"],
            yerr=df["cpu_usr_std"],
            color=cpu_color,
            linestyle="-",
            linewidth=line_width,
            marker="o",
            markersize=marker_size,
            markerfacecolor="white",
            markeredgecolor=cpu_color,
            markeredgewidth=2.0,
            capsize=4,
            capthick=1.2,
            label="Controller CPU (%usr)",
            zorder=3
        )
    else:
        line1 = ax1.plot(
            x_positions,
            df["cpu_usr_mean"],
            color=cpu_color,
            linestyle="-",
            linewidth=line_width,
            marker="o",
            markersize=marker_size,
            markerfacecolor="white",
            markeredgecolor=cpu_color,
            markeredgewidth=2.0,
            label="Controller CPU (%usr)",
            zorder=3
        )[0]

    # 2. Secondary Y-Axis (Right): Controller Memory (MB)
    if show_error_bars and "rss_mb_std" in df.columns:
        line2 = ax2.errorbar(
            x_positions,
            df["rss_mb_mean"],
            yerr=df["rss_mb_std"],
            color=mem_color,
            linestyle="--",
            linewidth=line_width,
            marker="s",
            markersize=marker_size,
            markerfacecolor="white",
            markeredgecolor=mem_color,
            markeredgewidth=2.0,
            capsize=4,
            capthick=1.2,
            label="Controller Memory (RSS)",
            zorder=3
        )
    else:
        line2 = ax2.plot(
            x_positions,
            df["rss_mb_mean"],
            color=mem_color,
            linestyle="--",
            linewidth=line_width,
            marker="s",
            markersize=marker_size,
            markerfacecolor="white",
            markeredgecolor=mem_color,
            markeredgewidth=2.0,
            label="Controller Memory (RSS)",
            zorder=3
        )[0]

    # X-Axis styling
    ax1.set_xlabel("Number of Network Servers ($M$)", fontsize=13, fontweight="bold", labelpad=8)
    ax1.set_xticks(x_positions)
    ax1.set_xticklabels(m_labels, fontsize=12)
    ax1.set_xlim(-0.4, len(x_positions) - 0.6)

    # Primary Y-Axis (Left) styling
    ax1.set_ylabel("Controller CPU Utilization (%)", fontsize=13, color=cpu_color, fontweight="bold", labelpad=8)
    ax1.tick_params(axis="y", labelcolor=cpu_color, labelsize=11)
    ax1.tick_params(axis="x", labelsize=12)
    ax1.set_ylim(bottom=0)
    max_cpu = df["cpu_usr_mean"].max()
    ax1.set_ylim(0, max(max_cpu * 1.35, 10.0))

    # Secondary Y-Axis (Right) styling
    ax2.set_ylabel("Controller Memory (MB)", fontsize=13, color=mem_color, fontweight="bold", labelpad=8)
    ax2.tick_params(axis="y", labelcolor=mem_color, labelsize=11)
    ax2.set_ylim(bottom=0)
    max_mem = df["rss_mb_mean"].max()
    ax2.set_ylim(0, max(max_mem * 1.35, 20.0))

    # Grid: aligned with primary left Y-axis, placed behind data
    ax1.set_axisbelow(True)
    ax1.grid(True, linestyle="--", linewidth=0.7, alpha=0.5, color="#888888")
    ax2.grid(False)

    # Combined single-box legend
    handles = [line1, line2]
    labels = [h.get_label() for h in handles]
    legend = ax1.legend(
        handles,
        labels,
        loc="upper left",
        frameon=True,
        facecolor="white",
        edgecolor="#cccccc",
        framealpha=0.95,
        fontsize=11,
        borderpad=0.6,
        handlelength=2.5,
        handletextpad=0.8
    )
    legend.get_frame().set_boxstyle("round,pad=0.4")

    # Tight layout and save
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"\n[+] Successfully generated publication figures:")
    print(f"    - PDF: {output_pdf}")
    print(f"    - PNG: {output_png} (300 DPI)")


def generate_mock_logs_if_needed(log_dir: str, m_values: list):
    """
    Creates mock pidstat log files for missing M values to allow immediate testing.
    """
    os.makedirs(log_dir, exist_ok=True)
    
    for m in m_values:
        filepath = os.path.join(log_dir, f"controller_scale_M{m}.log")
        if os.path.exists(filepath):
            continue
            
        print(f"[*] Generating synthetic sample log for testing: {filepath}")
        base_cpu = 3.2 + 0.24 * m
        base_rss_kb = (28.0 + 0.26 * m) * 1024.0
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("Linux 7.0.0-28-generic (p4dev) \t08/17/2026 \t_x86_64_\t(9 CPU)\n\n")
            for i in range(120):
                cpu_val = max(0.5, np.random.normal(base_cpu, 1.2))
                sys_val = max(0.1, np.random.normal(1.5, 0.4))
                tot_cpu = cpu_val + sys_val
                rss_val = int(max(1024, np.random.normal(base_rss_kb, 128)))
                vsz_val = 1309236
                
                # CPU entry
                f.write(f"12:{i//60:02d}:{i%60:02d} PM   UID       PID    %usr %system  %guest   %wait    %CPU   CPU  Command\n")
                f.write(f"12:{i//60:02d}:{(i+1)%60:02d} PM  1000     12241   {cpu_val:5.2f}    {sys_val:4.2f}    0.00    0.50   {tot_cpu:5.2f}     4  python3\n\n")
                
                # MEM entry
                f.write(f"12:{i//60:02d}:{i%60:02d} PM   UID       PID  minflt/s  majflt/s     VSZ     RSS   %MEM  Command\n")
                f.write(f"12:{i//60:02d}:{(i+1)%60:02d} PM  1000     12241      0.00      0.00 {vsz_val}   {rss_val}   0.45  python3\n\n")


def main():
    parser = argparse.ArgumentParser(
        description="Parse controller scalability logs and plot dual-axis CPU and Memory chart."
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="logs",
        help="Directory containing the controller_scale_M*.log files (default: logs/)"
    )
    parser.add_argument(
        "--m-values",
        type=int,
        nargs="+",
        default=[2, 4, 8, 16, 32],
        help="Cluster sizes M to parse and plot (default: 2 4 8 16 32)"
    )
    parser.add_argument(
        "--output-pdf",
        type=str,
        default="controller_scalability.pdf",
        help="Output PDF path (default: controller_scalability.pdf)"
    )
    parser.add_argument(
        "--output-png",
        type=str,
        default="controller_scalability.png",
        help="Output PNG path (default: controller_scalability.png)"
    )
    parser.add_argument(
        "--error-bars",
        action="store_true",
        help="Display standard deviation error bars on the plot"
    )
    parser.add_argument(
        "--generate-mock",
        action="store_true",
        help="Generate synthetic logs for any missing M values to test the pipeline"
    )

    args = parser.parse_args()

    print("=" * 70)
    print(" Controller Scalability Data Parser & Dual-Axis Plotter")
    print("=" * 70)

    # Check for missing logs and optionally generate mock data
    missing = [m for m in args.m_values if not os.path.exists(os.path.join(args.log_dir, f"controller_scale_M{m}.log"))]
    if missing:
        if args.generate_mock:
            print(f"[*] Generating mock data for missing M values: {missing}")
            generate_mock_logs_if_needed(args.log_dir, missing)
        else:
            print(f"[INFO] Missing log files for M={missing}. You can run with `--generate-mock` to synthesize test logs.")

    # 1. Parse Data
    df = load_scalability_data(args.log_dir, args.m_values)

    if df.empty:
        print("[ERROR] No data could be extracted. Exiting.")
        sys.exit(1)

    # 2. Print Summary Table
    print("\n" + "=" * 70)
    print(" Summary of Extracted Metrics:")
    print("=" * 70)
    summary_cols = ["M", "cpu_usr_mean", "cpu_usr_std", "rss_mb_mean", "rss_mb_std", "sample_count_cpu"]
    df_display = df[summary_cols].copy()
    df_display.columns = ["M (Nodes)", "CPU %usr (Avg)", "CPU %usr (Std)", "Memory MB (Avg)", "Memory MB (Std)", "Samples"]
    print(df_display.to_string(index=False, justify="center", float_format=lambda x: f"{x:.2f}"))
    print("=" * 70)

    # 3. Plot Data
    plot_dual_axis_scalability(
        df,
        output_pdf=args.output_pdf,
        output_png=args.output_png,
        show_error_bars=args.error_bars
    )


if __name__ == "__main__":
    main()
