# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
# ]
# ///

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
        expected_requests = rps * 10.0

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
    show_latency = any(s.p99_latency_ms > 0 for e in experiments for s in e.steps)
    rows = 2 if show_latency else 3

    fig, axes = plt.subplots(rows, 1, figsize=(10, 4 * rows), sharex=True)
    if rows == 1:
        axes = [axes]

    # --- NORMALIZATION SETUP ---
    # Extract the baseline DataFrame to use as the 100% denominator
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
            # Map the baseline watts to the corresponding RPS in the current dataframe
            mapped_baseline = df["target_rps"].map(baseline_df)
            df["plot_power"] = (df["cluster_watts"] / mapped_baseline) * 100
            y_label_power = f"Normalized Power (% of {baseline_name})"
        else:
            df["plot_power"] = df["cluster_watts"]
            y_label_power = "Cluster Power (Watts)"

        curr = 0

        # 1. Latency
        if show_latency:
            axes[curr].plot(
                df["target_rps"],
                df["p99_latency_ms"],
                exp.line_style,
                color=exp.color,
                label=exp.name,
            )
            axes[curr].set_ylabel("P99 Latency (ms)")
            axes[curr].set_title("System Latency (Lower is Better)")
            curr += 1

        # 2. Power
        axes[curr].plot(df["target_rps"], df["cluster_watts"],
                      exp.line_style, color=exp.color, label=exp.name)
        axes[curr].set_ylabel("Cluster Power (Watts)")
        axes[curr].set_title("Energy Consumption")
        curr += 1

        # # 3. Power Normalized
        # axes[curr].plot(
        #     df["target_rps"],
        #     df["plot_power"],
        #     exp.line_style,
        #     color=exp.color,
        #     label=exp.name,
        # )
        # axes[curr].set_ylabel(y_label_power)
        # axes[curr].set_title("Energy Consumption (Normalized)")
        # axes[curr].set_ylim(0, 100)  # Force Y-axis limits here
        # axes[curr].yaxis.set_major_locator(ticker.MultipleLocator(10))
        # curr += 1

        # # 4. PDP
        # axes[curr].plot(df["target_rps"], df["quality"],
        #               exp.line_style, color=exp.color, label=exp.name)
        # axes[curr].set_ylabel("PDP")
        # axes[curr].set_title("Quality (Higher is Better)")

    axes[-1].set_xlabel("Target RPS")

    for ax in axes:
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.legend()

    plt.tight_layout()
    plt.savefig(output, format="png", bbox_inches="tight")
    print(f"Success: {output} generated.")


def main():
    try:
        data = [
             parse_logs(
                "Round Robin",
                "red",
                "--s",
                "simulation_data/logs/client_sift_experiment.csv",
                ["simulation_data/logs/h2_energy.csv", "simulation_data/logs/h3_energy.csv"],
            ),
            # parse_logs(
            #     "Round Robin",
            #     "red",
            #     "--s",
            #     "data/roundrobin/client_sift_experiment.csv",
            #     ["data/roundrobin/h2_energy.csv", "data/roundrobin/h3_energy.csv"],
            # ),
            # parse_logs(
            #     "Least Utilized",
            #     "orange",
            #     "--s",
            #     "data/leastu/client_sift_experiment.csv",
            #     ["data/leastu/h2_energy.csv", "data/leastu/h3_energy.csv"],
            # ),
            # parse_logs(
            #     "Energy Aware - Marginal",
            #     "green",
            #     "-o",
            #     "data/marginal/client_sift_experiment.csv",
            #     ["data/marginal/h2_energy.csv", "data/marginal/h3_energy.csv"],
            # ),
            # parse_logs(
            #     "Energy Aware - WMC",
            #     "yellow",
            #     "-o",
            #     "data/wmc/client_sift_experiment.csv",
            #     ["data/wmc/h2_energy.csv", "data/wmc/h3_energy.csv"],
            # ),
        ]
        filename_and_current_timestamp = (
            f"main_experiment_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.png"
        )

        max_rps_cutoff = 4000

        # Passed 'Round Robin' implicitly as the default baseline_name
        plot_comparison(
            data,
            filename_and_current_timestamp,
            max_plot_rps=max_rps_cutoff,
            # baseline_name="Energy Aware - Marginal",
        )
    except Exception as e:
        print(f"Pipeline Failed: {e}")


if __name__ == "__main__":
    main()
