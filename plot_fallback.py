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
      (a) P99 Latency (ms) vs Target Load (RPS)
      (b) Cluster Energy Consumption (Watts) vs Target Load (RPS)
      (c) Throughput Capacity (RPS) vs Target Load (RPS)
      (Optional) Normalized Energy Consumption (% of Baseline)

Usage:
    uv run plot_fallback.py
    # or with custom options:
    uv run plot_fallback.py --data-dir paper/fallback --output paper/fallback.pdf
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


def plot_comparison(
    experiments: List[Experiment],
    output: str = "paper/fallback.pdf",
    max_plot_rps: Optional[int] = None,
    baseline_name: Optional[str] = None,
):
    """
    Total function: Guarantees the plot based on the existence of Experiment types.
    Allows capping the x-axis via max_plot_rps.
    Optionally normalizes power usage against a specified baseline experiment.
    """
    # Publication style configuration matching plot_main_exp_filter.py
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

    show_latency = any(s.p99_latency_ms > 0 for e in experiments for s in e.steps)
    has_baseline = baseline_name is not None and any(e.name == baseline_name for e in experiments)

    # 3 rows if baseline normalization is enabled, 2 rows for standard P99/Power
    rows = (3 if has_baseline else 2) if show_latency else (2 if has_baseline else 1)

    fig, axes = plt.subplots(rows, 1, figsize=(7.5, 2.7 * rows), sharex=True, dpi=300)
    if rows == 1:
        axes = [axes]

    # --- NORMALIZATION SETUP ---
    baseline_df = None
    if has_baseline:
        baseline_exp = next((e for e in experiments if e.name == baseline_name), None)
        if baseline_exp:
            baseline_df = baseline_exp.to_df().set_index("target_rps")["cluster_watts"]
        else:
            print(f"Warning: Baseline '{baseline_name}' not found. Plotting without normalization.")

    for exp in experiments:
        df = exp.to_df()
        if df.empty:
            continue

        # Filter the DataFrame if a maximum RPS is provided
        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]

        # --- APPLY NORMALIZATION ---
        if baseline_df is not None:
            mapped_baseline = df["target_rps"].map(baseline_df)
            df["plot_power"] = (df["cluster_watts"] / mapped_baseline) * 100

        line_fmt = f"{exp.line_style}{exp.marker}" if exp.marker else exp.line_style

        curr = 0

        # 1. Latency
        if show_latency:
            axes[curr].plot(
                df["target_rps"],
                df["p99_latency_ms"],
                line_fmt,
                color=exp.color,
                label=exp.name,
                linewidth=1.8,
                markersize=5.0,
                markevery=2,
            )
            curr += 1

        # 2. Power
        axes[curr].plot(
            df["target_rps"],
            df["cluster_watts"],
            line_fmt,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=2,
        )
        curr += 1

        # 3. Power Normalized (if baseline is specified)
        if has_baseline and baseline_df is not None:
            axes[curr].plot(
                df["target_rps"],
                df["plot_power"],
                line_fmt,
                color=exp.color,
                label=exp.name,
                linewidth=1.8,
                markersize=5.0,
                markevery=2,
            )
            curr += 1



    # --- SET AXIS LABELS AND TITLES ---
    idx = 0
    if show_latency:
        axes[idx].set_ylabel("P99 Latency (ms)", fontsize=11, fontweight="bold")
        axes[idx].set_title("(a) System Latency (Lower is Better)", fontsize=12, fontweight="bold")
        idx += 1

    axes[idx].set_ylabel("Cluster Power (Watts)", fontsize=11, fontweight="bold")
    letter = "(b)" if show_latency else "(a)"
    axes[idx].set_title(f"{letter} Energy Consumption", fontsize=12, fontweight="bold")
    idx += 1

    if has_baseline and baseline_df is not None:
        y_label_power = f"Norm. Power (% of {baseline_name})"
        axes[idx].set_ylabel(y_label_power, fontsize=11, fontweight="bold")
        letter = "(c)" if show_latency else "(b)"
        axes[idx].set_title(f"{letter} Energy Consumption (Normalized)", fontsize=12, fontweight="bold")
        axes[idx].set_ylim(0, 115)
        axes[idx].yaxis.set_major_locator(ticker.MultipleLocator(20))
        idx += 1

    axes[-1].set_xlabel("Target RPS", fontsize=11, fontweight="bold")

    for ax in axes:
        ax.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
        
    # Legend only on the first plot (Latency) to avoid straddling data
    axes[0].legend(fontsize=9.5, framealpha=0.92, loc="upper left")

    # Save output figures
    output_pdf = output if output.endswith(".pdf") else f"{output}.pdf"
    output_png = output_pdf.replace(".pdf", ".png")

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[+] Success: Figures saved to:\n    - {output_pdf}\n    - {output_png}")


def main():
    parser = argparse.ArgumentParser(
        description="Plot WMC Fallback Threshold Experiment Results (P99 Latency, Energy Consumption, and Throughput)."
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="paper/fallback",
        help="Directory containing fallback subdirectories (default: paper/fallback)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="paper/fallback.pdf",
        help="Output PDF/PNG file path (default: paper/fallback.pdf)",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="Optional baseline name for normalized power (e.g. 'Fallback 100%' or 'Fallback 50%')",
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
    print(" WMC Fallback Threshold Experiment Plotter")
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

    plot_comparison(
        experiments=experiments,
        output=args.output,
        max_plot_rps=args.max_rps,
        baseline_name=args.baseline,
    )


if __name__ == "__main__":
    main()
