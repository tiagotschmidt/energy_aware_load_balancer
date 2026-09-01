# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
#   "numpy",
# ]
# ///
"""
WMC Fallback Experiment Plotting Script
==========================================================================
Description:
    Parses client workload logs and server energy telemetry from the WMC
    (Weighted Marginal Cost) Fallback safety threshold experiments
    across various threshold levels (e.g. 50%, 60%, 70%, 80%, 90%, 100%).
    
    Generates publication-quality figures for:
      1. P99 Latency (ms) vs Target Load (RPS)
      2. Cluster Energy Consumption (Watts) vs Target Load (RPS)
      (Optional) Normalized Energy Consumption (% of Baseline)

Usage:
    uv run plot_fallback.py
    # or with custom options:
    uv run plot_fallback.py --data-dir paper/fallback --output-dir paper/
"""

import os
import sys
import glob
import argparse
from typing import List, Optional
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from pydantic import BaseModel


class TelemetryStep(BaseModel):
    """
    A single point of truth. If this object exists,
    the math has already been validated.
    """
    target_rps: int
    p99_latency_ms: float
    actual_throughput: float
    cluster_watts: float
    quality: float = 0.0


class Experiment(BaseModel):
    """The result of a full parsing pass."""
    name: str
    color: str
    line_style: str
    marker: str = "o"
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([s.model_dump() for s in self.steps])


def calculate_sla_cost(latency: float, power: float, sla_limit: float = 200.0, penalty_weight: float = 1.0) -> float:
    """
    Calculates the SLA-Constrained Cost.
    Cost = Power + Penalty
    Penalty = 0 if Latency <= SLA, else it scales sharply.
    """
    if latency <= sla_limit:
        return power
    else:
        violation_amount = latency - sla_limit
        return power + (violation_amount * penalty_weight)


def parse_logs(
    name: str,
    color: str,
    style: str,
    marker: str,
    client_path: str,
    server_paths: List[str],
) -> Optional[Experiment]:
    """
    PARSE, DON'T VALIDATE.
    This function acts as the airlock.
    """
    if not os.path.exists(client_path):
        print(f"[WARN] Missing client log: {client_path}")
        return None

    try:
        df_c = pd.read_csv(client_path)
        node_dfs = [pd.read_csv(p) for p in server_paths if os.path.exists(p)]
    except Exception as e:
        print(f"[ERROR] IO Error in {name}: {e}")
        return None

    if "target_rate" not in df_c.columns:
        print(f"[ERROR] Missing 'target_rate' in {client_path}")
        return None

    steps = []
    # Sort groups to process RPS in ascending order
    sorted_groups = sorted(df_c.groupby("target_rate"), key=lambda x: x[0])

    for rps, group in sorted_groups:
        t_start, t_end = group["timestamp"].min(), group["timestamp"].max()
        duration = t_end - t_start

        if duration <= 0:
            continue

        ok_mask = group["status"] == "OK"
        actual_requests = len(group[ok_mask])

        # --- DROP DETECTION ---
        # Calculate expected requests based on the 60-second execution window
        expected_requests = rps * 60.0

        # Allow a 1% tolerance for window boundary timing differences.
        # Break the loop to stop processing once drops are detected.
        if expected_requests > 0 and actual_requests < (expected_requests * 0.99):
            print(
                f"[{name}] Drop detected at {rps} RPS (Expected: ~{expected_requests:.0f}, Got: {actual_requests}). Capping plot here."
            )
            break

        # Latency Parsing (P99 Latency)
        p99 = group.loc[ok_mask, "latency_ms"].quantile(0.99) if ok_mask.any() else 0.0

        # Power Parsing (Aggregating across active server nodes)
        total_power = sum(
            ndf.loc[
                (ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end),
                "power_watts",
            ].mean()
            or 0.0
            for ndf in node_dfs
        )

        # Actual Throughput (RPS)
        actual_tput = actual_requests / duration

        steps.append(
            TelemetryStep(
                target_rps=int(rps),
                p99_latency_ms=float(p99),
                actual_throughput=float(actual_tput),
                cluster_watts=float(total_power),
                quality=calculate_sla_cost(p99, total_power),
            )
        )

    return Experiment(name=name, color=color, line_style=style, marker=marker, steps=steps)


def plot_latency_metric(
    experiments: List[Experiment],
    output_pdf: str = "paper/fallback_latency.pdf",
    output_png: str = "paper/fallback_latency.png",
    max_plot_rps: Optional[int] = None,
):
    """
    Generates a standalone publication figure for:
      P99 Latency (ms) vs Target Load
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

    fig, ax = plt.subplots(figsize=(6.8, 4.6), dpi=300)

    for exp in experiments:
        df = exp.to_df()
        if df.empty:
            continue

        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]

        line_fmt = f"{exp.line_style}{exp.marker}" if exp.marker else exp.line_style

        ax.plot(
            df["target_rps"],
            df["p99_latency_ms"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=2,
        )

    ax.set_xlabel("Target Load (RPS)", fontsize=11, fontweight="bold")
    ax.set_ylabel("P99 Latency (ms)", fontsize=11, fontweight="bold")
    ax.set_title("System Latency", fontsize=12, fontweight="bold")
    ax.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
    ax.legend(fontsize=9.5, framealpha=0.92, loc="upper left")

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[+] Latency figure generated successfully:\n    - {output_pdf}\n    - {output_png}")


def plot_power_metric(
    experiments: List[Experiment],
    output_pdf: str = "paper/fallback_power.pdf",
    output_png: str = "paper/fallback_power.png",
    max_plot_rps: Optional[int] = None,
):
    """
    Generates a standalone publication figure for:
      Cluster Energy Consumption (Watts) vs Target Load
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

    fig, ax = plt.subplots(figsize=(6.8, 4.6), dpi=300)

    for exp in experiments:
        df = exp.to_df()
        if df.empty:
            continue

        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]

        line_fmt = f"{exp.line_style}{exp.marker}" if exp.marker else exp.line_style

        ax.plot(
            df["target_rps"],
            df["cluster_watts"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=2,
        )

    ax.set_xlabel("Target Load (RPS)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Cluster Power (Watts)", fontsize=11, fontweight="bold")
    ax.set_title("Cluster Average Power Consumption", fontsize=12, fontweight="bold")
    ax.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
    ax.legend(fontsize=9.5, framealpha=0.92, loc="upper left")

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[+] Power figure generated successfully:\n    - {output_pdf}\n    - {output_png}")


def plot_power_normalized_metric(
    experiments: List[Experiment],
    baseline_name: str,
    output_pdf: str = "paper/fallback_normalized.pdf",
    output_png: str = "paper/fallback_normalized.png",
    max_plot_rps: Optional[int] = None,
):
    """
    Generates a standalone publication figure for:
      Normalized Energy Consumption (% of Baseline) vs Target Load
    """
    baseline_exp = next((e for e in experiments if e.name == baseline_name), None)
    if not baseline_exp:
        print(f"Warning: Baseline '{baseline_name}' not found. Cannot plot normalized power.")
        return

    baseline_df = baseline_exp.to_df().set_index("target_rps")["cluster_watts"]

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

    fig, ax = plt.subplots(figsize=(6.8, 4.6), dpi=300)

    for exp in experiments:
        df = exp.to_df()
        if df.empty:
            continue

        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]

        mapped_baseline = df["target_rps"].map(baseline_df)
        df["plot_power"] = (df["cluster_watts"] / mapped_baseline) * 100

        line_fmt = f"{exp.line_style}{exp.marker}" if exp.marker else exp.line_style

        ax.plot(
            df["target_rps"],
            df["plot_power"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=2,
        )

    ax.set_xlabel("Target Load (RPS)", fontsize=11, fontweight="bold")
    y_label_power = f"Norm. Power (% of {baseline_name})"
    ax.set_ylabel(y_label_power, fontsize=11, fontweight="bold")
    ax.set_title("Energy Consumption (Normalized)", fontsize=12, fontweight="bold")
    ax.set_ylim(0, 115)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(20))
    ax.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
    ax.legend(fontsize=9.5, framealpha=0.92, loc="upper left")

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[+] Normalized Power figure generated successfully:\n    - {output_pdf}\n    - {output_png}")


def main():
    parser = argparse.ArgumentParser(
        description="Plot WMC Fallback Threshold Experiment Results into separate publication figures."
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="paper/fallback",
        help="Directory containing fallback subdirectories (default: paper/fallback)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="paper",
        help="Output directory to save generated PDF and PNG figures (default: paper)",
    )
    parser.add_argument(
        "--latency-pdf",
        type=str,
        default=None,
        help="Custom path for Latency PDF (default: <output-dir>/fallback_latency.pdf)",
    )
    parser.add_argument(
        "--latency-png",
        type=str,
        default=None,
        help="Custom path for Latency PNG (default: <output-dir>/fallback_latency.png)",
    )
    parser.add_argument(
        "--power-pdf",
        type=str,
        default=None,
        help="Custom path for Power PDF (default: <output-dir>/fallback_power.pdf)",
    )
    parser.add_argument(
        "--power-png",
        type=str,
        default=None,
        help="Custom path for Power PNG (default: <output-dir>/fallback_power.png)",
    )
    parser.add_argument(
        "--normalized-pdf",
        type=str,
        default=None,
        help="Custom path for Normalized Power PDF (default: <output-dir>/fallback_normalized.pdf)",
    )
    parser.add_argument(
        "--normalized-png",
        type=str,
        default=None,
        help="Custom path for Normalized Power PNG (default: <output-dir>/fallback_normalized.png)",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="Optional baseline name for normalized power (e.g. 'Fallback 100% (No Fallback)' or 'Round Robin (Reference)')",
    )
    parser.add_argument(
        "--max-rps",
        type=int,
        default=None,
        help="Optional maximum RPS to plot",
    )
    parser.add_argument(
        "--thresholds",
        nargs="+",
        default=None,
        help="Specific threshold subfolders to plot (e.g. 50perc 70perc 90perc 100perc)",
    )

    args = parser.parse_args()

    # Determine file paths
    os.makedirs(args.output_dir, exist_ok=True)
    latency_pdf = args.latency_pdf or os.path.join(args.output_dir, "fallback_latency.pdf")
    latency_png = args.latency_png or os.path.join(args.output_dir, "fallback_latency.png")
    power_pdf = args.power_pdf or os.path.join(args.output_dir, "fallback_power.pdf")
    power_png = args.power_png or os.path.join(args.output_dir, "fallback_power.png")
    normalized_pdf = args.normalized_pdf or os.path.join(args.output_dir, "fallback_normalized.pdf")
    normalized_png = args.normalized_png or os.path.join(args.output_dir, "fallback_normalized.png")

    # Fallback configuration definitions (matching IEEE/publication palette)
    all_configs = [
        {
            "name": "Fallback 50%",
            "folder": "50perc",
            "color": "#1f77b4",  # Blue
            "style": "-",
            "marker": "o",
        },
        #{
        #    "name": "Fallback 60%",
        #    "folder": "60perc",
        #    "color": "#17becf",  # Cyan
        #    "style": "--",
        #    "marker": "v",
        #},
        #{
        #    "name": "Fallback 70%",
        #    "folder": "70perc",
        #    "color": "#2ca02c",  # Green
        #    "style": "-.",
        #    "marker": "^",
        #},
        {
            "name": "Fallback 80%",
            "folder": "80perc",
            "color": "#ff7f0e",  # Orange
            "style": ":",
            "marker": "s",
        },
        {
            "name": "Fallback 90%",
            "folder": "90perc",
            "color": "#9467bd",  # Purple
            "style": "--",
            "marker": "d",
        },
        {
            "name": "Fallback 100% (No Fallback)",
            "folder": "100perc",
            "color": "#d62728",  # Red
            "style": "-",
            "marker": "x",
        },
        {
            "name": "Round Robin (Reference)",
            "folder": "hardware_sift/roundrobin",
            "color": "#7f7f7f",  # Gray
            "style": "--",
            "marker": "s",
            "is_reference": True,
        },
    ]

    # Filter configs if specific thresholds requested
    if args.thresholds:
        target_folders = [t.lower().replace("%", "perc") for t in args.thresholds]
        configs = [c for c in all_configs if c["folder"].lower() in target_folders]
    else:
        configs = all_configs

    # Also check if user pointed to absolute or relative dir
    base_dir = args.data_dir
    if not os.path.exists(base_dir) and os.path.exists(os.path.join(".", base_dir.lstrip("/"))):
        base_dir = os.path.join(".", base_dir.lstrip("/"))

    experiments: List[Experiment] = []

    print("=" * 70)
    print(" WMC Fallback Threshold Experiment Plotter (Separate Figures)")
    print("=" * 70)

    for cfg in configs:
        if cfg.get("is_reference"):
            # Go up one level from 'paper/fallback' to 'paper'
            folder_path = os.path.join(os.path.dirname(base_dir), cfg["folder"])
        else:
            folder_path = os.path.join(base_dir, cfg["folder"])
            
        client_file = os.path.join(folder_path, "client_sift_experiment.csv")
        server_files = [
            os.path.join(folder_path, "h2_energy.csv"),
            os.path.join(folder_path, "h3_energy.csv"),
        ]

        if not os.path.exists(client_file):
            print(f"[*] Skipping {cfg['name']} (not found at {folder_path})")
            continue

        print(f"[*] Parsing [{cfg['name']}] from {folder_path}...")
        exp = parse_logs(
            name=cfg["name"],
            color=cfg["color"],
            style=cfg["style"],
            marker=cfg["marker"],
            client_path=client_file,
            server_paths=server_files,
        )

        if exp and exp.steps:
            experiments.append(exp)
            print(
                f"    -> Loaded {len(exp.steps)} steps | Max RPS: {exp.steps[-1].target_rps} | "
                f"Peak Latency: {max(s.p99_latency_ms for s in exp.steps):.2f} ms | "
                f"Peak Power: {max(s.cluster_watts for s in exp.steps):.2f} W"
            )

    if not experiments:
        print("\n[ERROR] No valid experiment logs could be parsed. Check data paths.")
        sys.exit(1)

    # 1. Generate Latency Figure
    plot_latency_metric(
        experiments=experiments,
        output_pdf=latency_pdf,
        output_png=latency_png,
        max_plot_rps=args.max_rps,
    )

    # 2. Generate Power Figure
    plot_power_metric(
        experiments=experiments,
        output_pdf=power_pdf,
        output_png=power_png,
        max_plot_rps=args.max_rps,
    )

    # 3. Generate Normalized Power Figure if baseline provided
    if args.baseline:
        plot_power_normalized_metric(
            experiments=experiments,
            baseline_name=args.baseline,
            output_pdf=normalized_pdf,
            output_png=normalized_png,
            max_plot_rps=args.max_rps,
        )


if __name__ == "__main__":
    main()
