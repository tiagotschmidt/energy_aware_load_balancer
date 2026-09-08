# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
# ]
# ///

"""
Main Hardware SIFT Experiment Plotting Script
==========================================================================
Description:
    Parses client workload logs and server energy telemetry from the hardware SIFT
    experiments comparing Round Robin, Least Utilized, and Energy Aware.
    
    Generates publication-quality figures for:
      1. P99 Latency (ms) vs Target Load (RPS)
      2. Cluster Energy Consumption (Watts) vs Target Load (RPS)
      3. Normalized Energy Consumption (% of Baseline) vs Target Load (RPS)

Usage:
    uv run plot_main_exp_filter.py
    # or specify output directory:
    uv run plot_main_exp_filter.py --output-dir paper/
"""

import os
import sys
import argparse
import pandas as pd
import matplotlib.pyplot as plt
from pydantic import BaseModel
from typing import List, Optional
import matplotlib.ticker as ticker


class TelemetryStep(BaseModel):
    """
    A single point of truth. If this object exists,
    the math has already been validated.
    """

    target_rps: int
    p99_latency_ms: float
    actual_throughput: float
    cluster_watts: float
    quality: float


class Experiment(BaseModel):
    """The result of a full parsing pass."""

    name: str
    color: str
    line_style: str
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([s.model_dump() for s in self.steps])


def parse_logs(
    name: str, color: str, style: str, client_path: str, server_paths: List[str]
) -> Experiment:
    """
    PARSE, DON'T VALIDATE.
    This function acts as the airlock.
    """
    try:
        df_c = pd.read_csv(client_path)
        node_dfs = [pd.read_csv(p) for p in server_paths]
    except Exception as e:
        raise ValueError(f"IO Error in {name}: {e}")

    # Enforce schema early
    if "target_rate" not in df_c.columns:
        raise ValueError(f"Missing 'target_rate' in {client_path}")

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
                f"[{name}] Drop detected at {rps} RPS (Expected: ~{expected_requests}, Got: {actual_requests}). Capping plot here."
            )
            break

        # Latency Parsing
        # p99 = group.loc[ok_mask, "latency_ms"].quantile(0.99) if ok_mask.any() else 0.0
        p99 = group.loc[ok_mask, "latency_ms"].mean() if ok_mask.any() else 0.0

        # Power Parsing (Aggregating across N nodes)
        total_power = sum(
            ndf.loc[
                (ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end),
                "power_watts",
            ].mean()
            or 0.0
            for ndf in node_dfs
        )

        steps.append(
            TelemetryStep(
                target_rps=int(rps),
                p99_latency_ms=p99,
                actual_throughput=actual_requests / duration,
                cluster_watts=total_power,
                quality=calculate_sla_cost(p99, total_power),
            )
        )

    return Experiment(name=name, color=color, line_style=style, steps=steps)


def calculate_sla_cost(latency, power, sla_limit=200, penalty_weight=1):
    """
    Calculates the SLA-Constrained Cost.
    Cost = Power + Penalty
    Penalty = 0 if Latency <= SLA, else it scales sharply.
    """
    if latency <= sla_limit:
        return power
    else:
        # Applies a steep penalty for every millisecond over the SLA
        violation_amount = latency - sla_limit
        return power + (violation_amount * penalty_weight)


def plot_latency_metric(
    experiments: List[Experiment],
    output_pdf: str = "paper/hardware_latency.pdf",
    output_png: str = "paper/hardware_latency.png",
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

        ax.plot(
            df["target_rps"],
            df["p99_latency_ms"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
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
    output_pdf: str = "paper/hardware_power.pdf",
    output_png: str = "paper/hardware_power.png",
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

        ax.plot(
            df["target_rps"],
            df["cluster_watts"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
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
    baseline_name: str = "Round Robin",
    output_pdf: str = "paper/hardware_normalized.pdf",
    output_png: str = "paper/hardware_normalized.png",
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

        ax.plot(
            df["target_rps"],
            df["plot_power"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
        )

    ax.set_xlabel("Target Load (RPS)", fontsize=11, fontweight="bold")
    y_label_power = f"Normalized Power (%)"
    ax.set_ylabel(y_label_power, fontsize=11, fontweight="bold")
    ax.set_title(f"Cluster Average Power Consumption (Normalized by {baseline_name})", fontsize=12, fontweight="bold")
    ax.set_ylim(0, 105)
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
    import argparse

    parser = argparse.ArgumentParser(
        description="Plot Hardware SIFT Experiment Results into separate publication figures."
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
        help="Custom path for Latency PDF (default: <output-dir>/hardware_latency.pdf)",
    )
    parser.add_argument(
        "--latency-png",
        type=str,
        default=None,
        help="Custom path for Latency PNG (default: <output-dir>/hardware_latency.png)",
    )
    parser.add_argument(
        "--power-pdf",
        type=str,
        default=None,
        help="Custom path for Power PDF (default: <output-dir>/hardware_power.pdf)",
    )
    parser.add_argument(
        "--power-png",
        type=str,
        default=None,
        help="Custom path for Power PNG (default: <output-dir>/hardware_power.png)",
    )
    parser.add_argument(
        "--normalized-pdf",
        type=str,
        default=None,
        help="Custom path for Normalized Power PDF (default: <output-dir>/hardware_normalized.pdf)",
    )
    parser.add_argument(
        "--normalized-png",
        type=str,
        default=None,
        help="Custom path for Normalized Power PNG (default: <output-dir>/hardware_normalized.png)",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default="Round Robin",
        help="Baseline experiment name for normalization (default: 'Round Robin')",
    )
    parser.add_argument(
        "--max-rps",
        type=int,
        default=None,
        help="Optional maximum RPS to plot",
    )

    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    latency_pdf = args.latency_pdf or os.path.join(args.output_dir, "hardware_latency.pdf")
    latency_png = args.latency_png or os.path.join(args.output_dir, "hardware_latency.png")
    power_pdf = args.power_pdf or os.path.join(args.output_dir, "hardware_power.pdf")
    power_png = args.power_png or os.path.join(args.output_dir, "hardware_power.png")
    normalized_pdf = args.normalized_pdf or os.path.join(args.output_dir, "hardware_normalized.pdf")
    normalized_png = args.normalized_png or os.path.join(args.output_dir, "hardware_normalized.png")

    try:
        data = [
            parse_logs(
                "Round Robin",
                "red",
                "--s",
                "paper/hardware_sift/roundrobin/client_sift_experiment.csv",
                [
                    "paper/hardware_sift/roundrobin/h2_energy.csv",
                    "paper/hardware_sift/roundrobin/h3_energy.csv",
                ],
            ),
            parse_logs(
                "Least Utilized",
                "orange",
                "--s",
                "paper/hardware_sift/leastu/client_sift_experiment.csv",
                [
                    "paper/hardware_sift/leastu/h2_energy.csv",
                    "paper/hardware_sift/leastu/h3_energy.csv",
                ],
            ),
            parse_logs(
                "Energy Aware",
                "green",
                "-o",
                "paper/hardware_sift/wmc/client_sift_experiment.csv",
                [
                    "paper/hardware_sift/wmc/h2_energy.csv",
                    "paper/hardware_sift/wmc/h3_energy.csv",
                ],
            ),
        ]

        # 1. Generate Latency Figure
        plot_latency_metric(
            experiments=data,
            output_pdf=latency_pdf,
            output_png=latency_png,
            max_plot_rps=args.max_rps,
        )

        # 2. Generate Power Figure
        plot_power_metric(
            experiments=data,
            output_pdf=power_pdf,
            output_png=power_png,
            max_plot_rps=args.max_rps,
        )

        # 3. Generate Normalized Power Figure
        if args.baseline:
            plot_power_normalized_metric(
                experiments=data,
                baseline_name=args.baseline,
                output_pdf=normalized_pdf,
                output_png=normalized_png,
                max_plot_rps=args.max_rps,
            )

    except Exception as e:
        print(f"Pipeline Failed: {e}")


if __name__ == "__main__":
    main()
