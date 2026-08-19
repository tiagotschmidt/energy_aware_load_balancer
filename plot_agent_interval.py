# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
#     "pydantic",
# ]
# ///
"""
Agent Update Interval Performance & Overhead Plotting Script
==========================================================================
Description:
    Parses client workload logs, server energy telemetry, and pidstat agent
    resource logs across different agent update intervals (10s, 1s, 0.5s, 0.1s).
    Generates two publication-ready figures optimized for LaTeX inclusion:
      1. Main Performance Figure: P99 Latency, Cluster Energy, and Throughput
      2. Server Agent Overhead Figure: Demonstrates the lightweight footprint
         (CPU %usr < 0.4% and Memory RSS ~11.6 MB).

Usage:
    uv run plot_agent_interval.py
    # or specify output directory / paths:
    uv run plot_agent_interval.py --output-dir paper/
"""

import os
import sys
import glob
import argparse
from typing import List, Optional, Dict, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pydantic import BaseModel


# =========================================================================
# 1. Pydantic Data Models (Inherited from plot_main_exp_filter.py)
# =========================================================================
class TelemetryStep(BaseModel):
    """
    A single validated telemetry data point for a given target RPS step.
    """
    target_rps: int
    p99_latency_ms: float
    actual_throughput: float
    cluster_watts: float
    quality: float = 0.0


class Experiment(BaseModel):
    """
    Complete telemetry series for an experiment configuration.
    """
    name: str
    interval_sec: float
    folder: str
    color: str
    line_style: str
    marker: str
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([s.model_dump() for s in self.steps])


# =========================================================================
# 2. Parsing Functions
# =========================================================================
def parse_main_experiment(
    name: str,
    interval_sec: float,
    folder: str,
    color: str,
    style: str,
    marker: str,
    client_path: str,
    server_paths: List[str],
) -> Optional[Experiment]:
    """
    Parses client workload logs and server energy logs.
    Applies drop detection (60s execution window with 1% tolerance) and
    calculates P99 latency, actual throughput, and total cluster power.
    """
    if not os.path.exists(client_path):
        print(f"[WARN] Missing client log: {client_path}")
        return None

    try:
        df_c = pd.read_csv(client_path)
        node_dfs = [pd.read_csv(p) for p in server_paths if os.path.exists(p)]
    except Exception as e:
        print(f"[ERROR] Failed to read logs for {name}: {e}")
        return None

    if "target_rate" not in df_c.columns:
        print(f"[ERROR] 'target_rate' column missing in {client_path}")
        return None

    steps = []
    sorted_groups = sorted(df_c.groupby("target_rate"), key=lambda x: x[0])

    for rps, group in sorted_groups:
        t_start, t_end = group["timestamp"].min(), group["timestamp"].max()
        duration = t_end - t_start

        if duration <= 0:
            continue

        ok_mask = group["status"] == "OK"
        actual_requests = len(group[ok_mask])

        # --- DROP DETECTION ---
        expected_requests = rps * 60.0
        if expected_requests > 0 and actual_requests < (expected_requests * 0.99):
            print(
                f"[{name}] Drop detected at {rps} RPS "
                f"(Expected: ~{expected_requests:.0f}, Got: {actual_requests}). "
                f"Capping plot here."
            )
            break

        # 1. P99 Latency (ms)
        p99 = (
            group.loc[ok_mask, "latency_ms"].quantile(0.99)
            if ok_mask.any()
            else 0.0
        )

        # 2. Cluster Power (Watts) - Aggregated across active server nodes
        total_power = sum(
            ndf.loc[
                (ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end),
                "power_watts",
            ].mean()
            or 0.0
            for ndf in node_dfs
        )

        # 3. Actual Throughput (RPS)
        actual_tput = actual_requests / duration

        steps.append(
            TelemetryStep(
                target_rps=int(rps),
                p99_latency_ms=float(p99),
                actual_throughput=float(actual_tput),
                cluster_watts=float(total_power),
            )
        )

    return Experiment(
        name=name,
        interval_sec=interval_sec,
        folder=folder,
        color=color,
        line_style=style,
        marker=marker,
        steps=steps,
    )


def parse_agent_pidstat(filepath: str) -> Optional[Dict[str, Any]]:
    """
    Parses a pidstat -u -r log file from the Server Agent process.
    Extracts %usr (CPU user utilization) and RSS (Resident Set Size in KB -> MB).
    """
    if not os.path.exists(filepath):
        return None

    cpu_usr_values = []
    rss_mb_values = []

    current_section = None
    usr_col_idx = None
    rss_col_idx = None
    cmd_col_idx = None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line_str = line.strip()
            if (
                not line_str
                or line_str.startswith("Linux")
                or line_str.startswith("Average:")
            ):
                continue

            tokens = line_str.split()

            # Detect CPU header
            if "%usr" in tokens:
                current_section = "CPU"
                usr_col_idx = tokens.index("%usr")
                cmd_col_idx = (
                    tokens.index("Command") if "Command" in tokens else -1
                )
                continue
            # Detect Memory header
            elif "RSS" in tokens:
                current_section = "MEM"
                rss_col_idx = tokens.index("RSS")
                cmd_col_idx = (
                    tokens.index("Command") if "Command" in tokens else -1
                )
                continue

            # Check for python command filter if Command column is present
            if (
                cmd_col_idx != -1
                and len(tokens) > cmd_col_idx
                and "python" not in tokens[cmd_col_idx].lower()
            ):
                continue

            # Parse CPU sample
            if current_section == "CPU" and usr_col_idx is not None:
                try:
                    val = float(tokens[usr_col_idx])
                    cpu_usr_values.append(val)
                except (ValueError, IndexError):
                    pass

            # Parse Memory sample
            elif current_section == "MEM" and rss_col_idx is not None:
                try:
                    val_kb = float(tokens[rss_col_idx])
                    rss_mb_values.append(val_kb / 1024.0)  # Convert KB to MB
                except (ValueError, IndexError):
                    pass

    if not cpu_usr_values:
        return None

    cpu_arr = np.array(cpu_usr_values)
    mem_arr = np.array(rss_mb_values) if rss_mb_values else np.array([0.0])

    return {
        "cpu_usr_mean": float(np.mean(cpu_arr)),
        "cpu_usr_std": float(np.std(cpu_arr)),
        "rss_mb_mean": float(np.mean(mem_arr)),
        "rss_mb_std": float(np.std(mem_arr)),
        "sample_count": len(cpu_arr),
    }


# =========================================================================
# 3. Dedicated Plot 1: Main Performance (P99 Latency, Power, Throughput)
# =========================================================================
def plot_performance_metrics(
    experiments: List[Experiment],
    output_pdf: str = "agent_interval_performance.pdf",
    output_png: str = "agent_interval_performance.png",
    max_plot_rps: Optional[int] = None,
):
    """
    Generates a standalone 3-panel publication figure for:
      (a) P99 Latency (ms) vs Target Load
      (b) Cluster Energy Consumption (Watts) vs Target Load
      (c) Throughput Capacity (RPS) vs Target Load
    """
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
        "axes.edgecolor": "#333333",
        "axes.linewidth": 1.0,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.size": 4.5,
        "ytick.major.size": 4.5,
    })

    fig, axes = plt.subplots(3, 1, figsize=(7.2, 8.8), sharex=True, dpi=300)
    plt.subplots_adjust(hspace=0.22)

    ax_lat, ax_power, ax_tput = axes[0], axes[1], axes[2]

    for exp in experiments:
        df = exp.to_df()
        if df.empty:
            continue

        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]

        line_fmt = f"{exp.line_style}{exp.marker}"

        # 1. Latency
        ax_lat.plot(
            df["target_rps"],
            df["p99_latency_ms"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            markersize=5.0,
            linewidth=1.8,
            markevery=3,
        )

        # 2. Power
        ax_power.plot(
            df["target_rps"],
            df["cluster_watts"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            markersize=5.0,
            linewidth=1.8,
            markevery=3,
        )

        # 3. Throughput
        ax_tput.plot(
            df["target_rps"],
            df["actual_throughput"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            markersize=5.0,
            linewidth=1.8,
            markevery=3,
        )

    # Subplot 1: Latency Styling
    ax_lat.set_ylabel("P99 Latency (ms)", fontsize=11, fontweight="bold")
    ax_lat.set_title("(a) P99 Latency (Lower is Better)", fontsize=12, fontweight="bold")
    ax_lat.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
    ax_lat.legend(loc="upper left", fontsize=10, framealpha=0.92)

    # Subplot 2: Power Styling
    ax_power.set_ylabel("Cluster Power (Watts)", fontsize=11, fontweight="bold")
    ax_power.set_title("(b) Cluster Energy Consumption", fontsize=12, fontweight="bold")
    ax_power.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)

    # Subplot 3: Throughput Styling
    ax_tput.set_ylabel("Throughput (RPS)", fontsize=11, fontweight="bold")
    ax_tput.set_xlabel("Target Load (RPS)", fontsize=11, fontweight="bold")
    ax_tput.set_title("(c) Throughput Capacity", fontsize=12, fontweight="bold")
    ax_tput.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)

    # Save output figures
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"\n[+] Performance figure generated successfully:")
    print(f"    - PDF: {output_pdf}")
    print(f"    - PNG: {output_png}")


# =========================================================================
# 4. Dedicated Plot 2: Server Agent Overhead (Lightweight Demonstration)
# =========================================================================
def plot_agent_overhead(
    df_overhead: pd.DataFrame,
    all_interval_labels: List[str],
    output_pdf: str = "agent_interval_overhead.pdf",
    output_png: str = "agent_interval_overhead.png",
    show_error_bars: bool = False,
):
    """
    Generates a publication figure demonstrating the lightweight nature
    of the Server Agent across update intervals.
    Dual-axis:
      - Primary Y-Axis (Left, Blue): CPU Utilization (%usr), scaled to highlight < 0.5%
      - Secondary Y-Axis (Right, Orange): Memory Footprint (RSS in MB)
    """
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
        "axes.edgecolor": "#333333",
        "axes.linewidth": 1.0,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.size": 5.0,
        "ytick.major.size": 5.0,
    })

    fig, ax1 = plt.subplots(figsize=(6.8, 4.6), dpi=300)
    ax2 = ax1.twinx()

    cpu_color = "#1f77b4"  # IEEE Blue
    mem_color = "#ff7f0e"  # Publication Orange

    # Fixed categorical X-axis covering all target intervals
    x_positions = {label: idx for idx, label in enumerate(all_interval_labels)}
    x_ticks = np.arange(len(all_interval_labels))

    handles = []
    if not df_overhead.empty:
        df_plot = df_overhead.copy()
        df_plot["x_pos"] = df_plot["interval_label"].map(x_positions)
        df_plot = df_plot.dropna(subset=["x_pos"]).sort_values("x_pos")

        # 1. Primary Y-Axis (Left): CPU Utilization (%)
        if show_error_bars and "cpu_usr_std" in df_plot.columns:
            line1 = ax1.errorbar(
                df_plot["x_pos"],
                df_plot["cpu_usr_mean"],
                yerr=df_plot["cpu_usr_std"],
                color=cpu_color,
                linestyle="-",
                linewidth=2.4,
                marker="o",
                markersize=8.5,
                markerfacecolor="white",
                markeredgecolor=cpu_color,
                markeredgewidth=2.2,
                capsize=4,
                capthick=1.2,
                label="Agent CPU (%usr)",
                zorder=4,
            )
        else:
            line1 = ax1.plot(
                df_plot["x_pos"],
                df_plot["cpu_usr_mean"],
                color=cpu_color,
                linestyle="-",
                linewidth=2.4,
                marker="o",
                markersize=8.5,
                markerfacecolor="white",
                markeredgecolor=cpu_color,
                markeredgewidth=2.2,
                label="Agent CPU (%usr)",
                zorder=4,
            )[0]
        handles.append(line1)

        # Annotate CPU values to explicitly demonstrate lightweight footprint
        for _, row in df_plot.iterrows():
            # Place 1s and 0.5s below the line, and 0.1s above the point
            y_offset = 13 if row["cpu_usr_mean"] > 0.2 else -18
            ax1.annotate(
                f"{row['cpu_usr_mean']:.2f}%",
                (row["x_pos"], row["cpu_usr_mean"]),
                textcoords="offset points",
                xytext=(0, y_offset),
                ha="center",
                fontsize=9.5,
                fontweight="bold",
                color=cpu_color,
                bbox=dict(
                    boxstyle="round,pad=0.25",
                    facecolor="white",
                    edgecolor=cpu_color,
                    alpha=0.95,
                    linewidth=0.9,
                ),
                zorder=5,
            )

        # 2. Secondary Y-Axis (Right): Memory Footprint (MB RSS)
        if show_error_bars and "rss_mb_std" in df_plot.columns:
            line2 = ax2.errorbar(
                df_plot["x_pos"],
                df_plot["rss_mb_mean"],
                yerr=df_plot["rss_mb_std"],
                color=mem_color,
                linestyle="--",
                linewidth=2.4,
                marker="s",
                markersize=8.5,
                markerfacecolor="white",
                markeredgecolor=mem_color,
                markeredgewidth=2.2,
                capsize=4,
                capthick=1.2,
                label="Agent Memory (RSS)",
                zorder=4,
            )
        else:
            line2 = ax2.plot(
                df_plot["x_pos"],
                df_plot["rss_mb_mean"],
                color=mem_color,
                linestyle="--",
                linewidth=2.4,
                marker="s",
                markersize=8.5,
                markerfacecolor="white",
                markeredgecolor=mem_color,
                markeredgewidth=2.2,
                label="Agent Memory (RSS)",
                zorder=4,
            )[0]
        handles.append(line2)

        # Annotate Memory values above the square markers
        for _, row in df_plot.iterrows():
            ax2.annotate(
                f"{row['rss_mb_mean']:.1f} MB",
                (row["x_pos"], row["rss_mb_mean"]),
                textcoords="offset points",
                xytext=(0, 12),
                ha="center",
                fontsize=9.5,
                fontweight="bold",
                color=mem_color,
                bbox=dict(
                    boxstyle="round,pad=0.25",
                    facecolor="white",
                    edgecolor=mem_color,
                    alpha=0.95,
                    linewidth=0.9,
                ),
                zorder=5,
            )

    # Styling for X-Axis
    ax1.set_xticks(x_ticks)
    ax1.set_xticklabels(all_interval_labels, fontsize=12)
    ax1.set_xlim(-0.5, len(all_interval_labels) - 0.5)
    ax1.set_xlabel("Agent Update Interval", fontsize=12, fontweight="bold", labelpad=8)

    # Primary Y-Axis (Left): Scaled to 0 - 0.6% to visually show it is < 0.5% of CPU
    ax1.set_ylabel("Agent CPU Utilization (%)", fontsize=12, color=cpu_color, fontweight="bold", labelpad=8)
    ax1.tick_params(axis="y", labelcolor=cpu_color, labelsize=11)
    ax1.set_ylim(0, 0.60)
    ax1.grid(True, linestyle="--", linewidth=0.7, alpha=0.6, color="#888888")

    # Secondary Y-Axis (Right): Scaled to 0 - 20 MB to show steady minimal ~11.6 MB
    ax2.set_ylabel("Agent Memory Footprint (MB RSS)", fontsize=12, color=mem_color, fontweight="bold", labelpad=8)
    ax2.tick_params(axis="y", labelcolor=mem_color, labelsize=11)
    ax2.set_ylim(0, 20.0)
    ax2.grid(False)

    ax1.set_title("Server Agent Resource Overhead (< 0.4% CPU, ~11.6 MB RSS)", fontsize=12, fontweight="bold", pad=12)

    # Combined single legend box
    if handles:
        labels = [h.get_label() for h in handles]
        legend = ax1.legend(
            handles,
            labels,
            loc="upper left",
            fontsize=10.5,
            frameon=True,
            facecolor="white",
            edgecolor="#cccccc",
            framealpha=0.95,
        )
        legend.get_frame().set_boxstyle("round,pad=0.4")

    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"\n[+] Agent Overhead figure generated successfully:")
    print(f"    - PDF: {output_pdf}")
    print(f"    - PNG: {output_png}")


# =========================================================================
# 5. Main Entry Point
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Plot Agent Update Interval experiment results and server overhead into separate publication figures."
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default="paper/agent_interval",
        help="Base directory containing the interval results folders (default: paper/agent_interval)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="paper",
        help="Output directory to save generated PDF and PNG figures (default: paper)",
    )
    parser.add_argument(
        "--perf-pdf",
        type=str,
        default=None,
        help="Custom path for Performance PDF (default: <output-dir>/agent_interval_performance.pdf)",
    )
    parser.add_argument(
        "--perf-png",
        type=str,
        default=None,
        help="Custom path for Performance PNG (default: <output-dir>/agent_interval_performance.png)",
    )
    parser.add_argument(
        "--overhead-pdf",
        type=str,
        default=None,
        help="Custom path for Overhead PDF (default: <output-dir>/agent_interval_overhead.pdf)",
    )
    parser.add_argument(
        "--overhead-png",
        type=str,
        default=None,
        help="Custom path for Overhead PNG (default: <output-dir>/agent_interval_overhead.png)",
    )
    parser.add_argument(
        "--error-bars",
        action="store_true",
        help="Show standard deviation error bars on overhead plot",
    )
    parser.add_argument(
        "--max-rps",
        type=int,
        default=None,
        help="Optional cutoff for maximum RPS to plot",
    )

    args = parser.parse_args()

    # Determine file paths
    os.makedirs(args.output_dir, exist_ok=True)
    perf_pdf = args.perf_pdf or os.path.join(args.output_dir, "agent_interval_performance.pdf")
    perf_png = args.perf_png or os.path.join(args.output_dir, "agent_interval_performance.png")
    overhead_pdf = args.overhead_pdf or os.path.join(args.output_dir, "agent_interval_overhead.pdf")
    overhead_png = args.overhead_png or os.path.join(args.output_dir, "agent_interval_overhead.png")

    print("=" * 75)
    print(" Agent Update Interval Experiment Plotter (Separate Figures)")
    print("=" * 75)

    # Four interval values configuration
    interval_configs = [
        {
            "name": "Interval 10s",
            "short_label": "10s",
            "sec": 10.0,
            "folder": "10sec",
            "color": "#d62728",  # Crimson Red
            "style": ":",
            "marker": "s",
        },
        {
            "name": "Interval 1.0s",
            "short_label": "1s",
            "sec": 1.0,
            "folder": "1sec",
            "color": "#ff7f0e",  # Orange
            "style": "--",
            "marker": "o",
        },
        {
            "name": "Interval 0.5s",
            "short_label": "0.5s",
            "sec": 0.5,
            "folder": "05sec",
            "color": "#1f77b4",  # Tech Blue
            "style": "-.",
            "marker": "^",
        },
        {
            "name": "Interval 0.1s",
            "short_label": "0.1s",
            "sec": 0.1,
            "folder": "01sec",
            "color": "#2ca02c",  # Forest Green
            "style": "-",
            "marker": "d",
        },
    ]

    experiments: List[Experiment] = []
    overhead_records = []

    # Process all intervals
    for cfg in interval_configs:
        folder_path = os.path.join(args.base_dir, cfg["folder"])
        print(f"\n[*] Processing [{cfg['name']}] in '{folder_path}'...")

        # 1. Main Telemetry Logs
        client_csv = os.path.join(folder_path, "client_sift_experiment.csv")
        server_csvs = [
            os.path.join(folder_path, "h2_energy.csv"),
            os.path.join(folder_path, "h3_energy.csv"),
        ]

        exp = parse_main_experiment(
            name=cfg["name"],
            interval_sec=cfg["sec"],
            folder=cfg["folder"],
            color=cfg["color"],
            style=cfg["style"],
            marker=cfg["marker"],
            client_path=client_csv,
            server_paths=server_csvs,
        )

        if exp and exp.steps:
            experiments.append(exp)
            print(
                f"    -> Parsed {len(exp.steps)} load steps "
                f"(Max RPS: {exp.steps[-1].target_rps}, "
                f"Peak Latency: {max(s.p99_latency_ms for s in exp.steps):.2f} ms)"
            )
        else:
            print(f"    -> [WARN] No valid telemetry steps loaded for {cfg['name']}.")

        # 2. Server Agent Overhead Logs (pidstat -u -r)
        log_files = glob.glob(os.path.join(folder_path, "*.log"))
        agent_logs = [f for f in log_files if "agent" in os.path.basename(f).lower()]
        target_log = agent_logs[0] if agent_logs else (log_files[0] if log_files else None)

        if target_log:
            stats = parse_agent_pidstat(target_log)
            if stats:
                stats["interval_label"] = cfg["short_label"]
                stats["interval_sec"] = cfg["sec"]
                stats["log_file"] = os.path.basename(target_log)
                overhead_records.append(stats)
                print(
                    f"    -> Overhead Log: {os.path.basename(target_log)} "
                    f"({stats['sample_count']} samples | "
                    f"CPU: {stats['cpu_usr_mean']:.2f}% | "
                    f"Memory: {stats['rss_mb_mean']:.2f} MB)"
                )
            else:
                print(f"    -> [WARN] Could not extract samples from {target_log}.")
        else:
            print(f"    -> [INFO] No agent log found for {cfg['name']} (skipping overhead entry).")

    if not experiments:
        print("\n[ERROR] No valid experiment telemetry found. Exiting.")
        sys.exit(1)

    df_overhead = pd.DataFrame(overhead_records)

    # 3. Print Summary Table
    if not df_overhead.empty:
        print("\n" + "=" * 75)
        print(" Summary of Extracted Server Agent Overhead:")
        print("=" * 75)
        cols_show = ["interval_label", "cpu_usr_mean", "cpu_usr_std", "rss_mb_mean", "rss_mb_std", "sample_count", "log_file"]
        df_disp = df_overhead[cols_show].copy()
        df_disp.columns = ["Interval", "CPU %usr (Avg)", "CPU %usr (Std)", "Memory MB (Avg)", "Memory MB (Std)", "Samples", "Log File"]
        print(df_disp.to_string(index=False, justify="center", float_format=lambda x: f"{x:.2f}"))
        print("=" * 75)

    all_labels = [c["short_label"] for c in interval_configs]

    # 4. Generate Plot 1: Performance Figure
    plot_performance_metrics(
        experiments=experiments,
        output_pdf=perf_pdf,
        output_png=perf_png,
        max_plot_rps=args.max_rps,
    )

    # 5. Generate Plot 2: Agent Overhead Figure
    plot_agent_overhead(
        df_overhead=df_overhead,
        all_interval_labels=all_labels,
        output_pdf=overhead_pdf,
        output_png=overhead_png,
        show_error_bars=args.error_bars,
    )


if __name__ == "__main__":
    main()
