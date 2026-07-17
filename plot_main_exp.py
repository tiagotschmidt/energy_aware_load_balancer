# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
# ]
# ///

import pandas as pd
import matplotlib.pyplot as plt
from pydantic import BaseModel, Field
from typing import List, Optional
from pathlib import Path


class TelemetryStep(BaseModel):
    """
    A single point of truth. If this object exists, 
    the math has already been validated.
    """
    target_rps: int
    p99_latency_ms: float
    actual_throughput: float
    cluster_watts: float

class Experiment(BaseModel):
    """The result of a full parsing pass."""
    name: str
    color: str
    line_style: str
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([s.model_dump() for s in self.steps])

def parse_logs(name: str, color: str, style: str, client_path: str, server_paths: List[str]) -> Experiment:
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
    for rps, group in df_c.groupby("target_rate"):
        t_start, t_end = group["timestamp"].min(), group["timestamp"].max()
        duration = t_end - t_start
        
        if duration <= 0: continue

        # Latency Parsing
        p99 = 0.0
        if "latency_ms" in group.columns:
            ok_mask = group["status"] == "OK"
            p99 = group.loc[ok_mask, "latency_ms"].quantile(0.99) if ok_mask.any() else 0.0

        # Power Parsing (Aggregating across N nodes)
        total_power = sum(
            ndf.loc[(ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end), "power_watts"].mean() or 0.0
            for ndf in node_dfs
        )

        steps.append(TelemetryStep(
            target_rps=int(rps),
            p99_latency_ms=p99,
            actual_throughput=len(group[group["status"] == "OK"]) / duration,
            cluster_watts=total_power
        ))

    return Experiment(name=name, color=color, line_style=style, steps=steps)


def plot_comparison(experiments: List[Experiment], output: str = "saturation_results.png", max_plot_rps: Optional[int] = None):
    """
    Total function: Guarantees the plot based on the existence of Experiment types.
    Allows capping the x-axis via max_plot_rps.
    """
    show_latency = any(s.p99_latency_ms > 0 for e in experiments for s in e.steps)
    rows = 1 if show_latency else 2
    
    fig, axes = plt.subplots(rows, 1, figsize=(10, 4 * rows), sharex=True)
    if rows == 1: axes = [axes]

    for exp in experiments:
        df = exp.to_df()
        
        # Filter the DataFrame if a maximum RPS is provided
        if max_plot_rps is not None:
            df = df[df["target_rps"] <= max_plot_rps]
            
        curr = 0
        
        # # 2. Latency
        # if show_latency:
        #     axes[curr].plot(df["target_rps"], df["p100_latency_ms"], 
        #                   exp.line_style, color=exp.color, label=exp.name)
        #     axes[curr].set_ylabel("P100 Latency (ms)")
        #     axes[curr].set_title("System Latency (Lower is Better)")
        #     curr += 2

        # # 3. Power
        # axes[curr].plot(df["target_rps"], df["cluster_watts"], 
        #               exp.line_style, color=exp.color, label=exp.name)
        # axes[curr].set_ylabel("Cluster Power (Watts)")
        # axes[curr].set_title("Energy Consumption")
        # curr += 1

        # 3. Throughput
        axes[curr].plot(df["target_rps"], df["actual_throughput"], 
                      exp.line_style, color=exp.color, label=exp.name)
        axes[curr].set_ylabel("Throughput (RPS)")
        axes[curr].set_title("Throughput Capacity")

    # Ideal line on throughput
    max_rps = max(s.target_rps for e in experiments for s in e.steps)
    if max_plot_rps is not None:
        max_rps = min(max_rps, max_plot_rps)
        
    axes[-1].plot([0, max_rps], [0, max_rps], "k:", alpha=0.3, label="Ideal")
    axes[-1].set_xlabel("Target RPS")
    
    for ax in axes:
        ax.grid(True, linestyle='--', alpha=0.6)
        ax.legend()

    plt.tight_layout()
    plt.savefig(output, format="png", bbox_inches="tight")
    print(f"Success: {output} generated.")


def main():
    try:
        data = [
            parse_logs("Round Robin", "red", "--s", 
                       "data/roundrobin/client_sift_experiment.csv", ["data/roundrobin/h2_energy.csv", "data/roundrobin/h3_energy.csv"]),
            # parse_logs("Least Utilized", "orange", "--s", 
            #            "data/leastu/client_sift_experiment.csv", ["data/leastu/h2_energy.csv", "data/leastu/h3_energy.csv"]),
            # parse_logs("Energy Aware - Marginal", "green", "-o", 
            #            "data/marginal/client_sift_experiment.csv", ["data/marginal/h2_energy.csv", "data/marginal/h3_energy.csv"])
        ]
        filename_and_current_timestamp = f"main_experiment_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.png"

        max_rps_cutoff = 5000
        
        plot_comparison(data, filename_and_current_timestamp, max_plot_rps=max_rps_cutoff)
    except Exception as e:
        print(f"Pipeline Failed: {e}")

if __name__ == "__main__":
    main()