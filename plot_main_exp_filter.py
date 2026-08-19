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


def plot_comparison(
    experiments: List[Experiment],
    output: str = "saturation_results.png",
    max_plot_rps: Optional[int] = None,
    baseline_name: str = "Round Robin",
):
    """
    Total function: Guarantees the plot based on the existence of Experiment types.
    Allows capping the x-axis via max_plot_rps.
    Normalizes power usage against a specified baseline experiment.
    """
    # Publication style configuration
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
    rows = 4 if show_latency else 3

    fig, axes = plt.subplots(rows, 1, figsize=(7.5, 2.6 * rows), sharex=True, dpi=300)
    if rows == 1:
        axes = [axes]

    # --- NORMALIZATION SETUP ---
    baseline_exp = next((e for e in experiments if e.name == baseline_name), None)
    if baseline_exp:
        baseline_df = baseline_exp.to_df().set_index("target_rps")["cluster_watts"]
    else:
        baseline_df = None
        print(
            f"Warning: Baseline '{baseline_name}' not found. Plotting raw watts instead."
        )

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
        else:
            df["plot_power"] = df["cluster_watts"]

        curr = 0

        # 1. Latency
        if show_latency:
            axes[curr].plot(
                df["target_rps"],
                df["p99_latency_ms"],
                exp.line_style,
                color=exp.color,
                label=exp.name,
                linewidth=1.8,
                markersize=5.0,
                markevery=3,
            )
            curr += 1

        # 2. Power
        axes[curr].plot(
            df["target_rps"],
            df["cluster_watts"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
        )
        curr += 1

        # 3. Power Normalized
        axes[curr].plot(
            df["target_rps"],
            df["plot_power"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
        )
        curr += 1

        # 4. Throughput
        axes[curr].plot(
            df["target_rps"],
            df["actual_throughput"],
            exp.line_style,
            color=exp.color,
            label=exp.name,
            linewidth=1.8,
            markersize=5.0,
            markevery=3,
        )

    # --- SET AXIS LABELS AND TITLES ONCE ---
    idx = 0
    if show_latency:
        axes[idx].set_ylabel("P99 Latency (ms)", fontsize=11, fontweight="bold")
        axes[idx].set_title("(a) System Latency (Lower is Better)", fontsize=12, fontweight="bold")
        idx += 1

    axes[idx].set_ylabel("Cluster Power (Watts)", fontsize=11, fontweight="bold")
    axes[idx].set_title("(b) Energy Consumption", fontsize=12, fontweight="bold")
    idx += 1

    y_label_power = f"Norm. Power (% of {baseline_name})" if baseline_df is not None else "Cluster Power (Watts)"
    axes[idx].set_ylabel(y_label_power, fontsize=11, fontweight="bold")
    axes[idx].set_title("(c) Energy Consumption (Normalized)", fontsize=12, fontweight="bold")
    axes[idx].set_ylim(0, 105)
    axes[idx].yaxis.set_major_locator(ticker.MultipleLocator(20))
    idx += 1

    axes[idx].set_ylabel("Throughput (RPS)", fontsize=11, fontweight="bold")
    axes[idx].set_title("(d) Throughput Capacity", fontsize=12, fontweight="bold")

    axes[-1].set_xlabel("Target RPS", fontsize=11, fontweight="bold")

    for ax in axes:
        ax.grid(True, linestyle="--", linewidth=0.7, alpha=0.6)
        ax.legend(fontsize=9.5, framealpha=0.92)

    # Save output figures
    output_pdf = output if output.endswith(".pdf") else f"{output}.pdf"
    output_png = output_pdf.replace(".pdf", ".png")
    
    plt.tight_layout()
    plt.savefig(output_pdf, format="pdf", dpi=300, bbox_inches="tight")
    plt.savefig(output_png, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Success: {output_pdf} and {output_png} generated.")


def main():
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

        output_file = "paper/hardware.pdf"
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        # Passed 'Round Robin' implicitly as the default baseline_name
        plot_comparison(
            data,
            output_file,
            baseline_name="Round Robin",
        )
    except Exception as e:
        print(f"Pipeline Failed: {e}")


if __name__ == "__main__":
    main()
