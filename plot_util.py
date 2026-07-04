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
from typing import List, Dict, Optional
from pathlib import Path

class TelemetryStep(BaseModel):
    """A single point of truth for a load step."""
    target_rps: int
    actual_throughput: float
    host_cpu: Dict[str, float]

class Experiment(BaseModel):
    """The result of a full parsing pass."""
    name: str
    color: str
    line_style: str
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.json_normalize([s.model_dump() for s in self.steps])

def parse_logs(name: str, color: str, style: str, client_path: str, server_paths: List[str], 
               cpu_col: str, min_rps: Optional[int] = None, max_rps: Optional[int] = None) -> Experiment:
    """Parses client logs for throughput and server logs for CPU utilization."""
    try:
        df_c = pd.read_csv(client_path)
        node_dfs = [pd.read_csv(p) for p in server_paths]
    except Exception as e:
        raise ValueError(f"IO Error in {name}: {e}")

    if "target_rate" not in df_c.columns:
        raise ValueError(f"Missing 'target_rate' in {client_path}")

    # --- NEW: Filter by target load limits ---
    if min_rps is not None:
        df_c = df_c[df_c["target_rate"] >= min_rps]
    if max_rps is not None:
        df_c = df_c[df_c["target_rate"] <= max_rps]

    host_names = [Path(p).stem.split('_')[0] for p in server_paths]
    
    steps = []
    for rps, group in df_c.groupby("target_rate"):
        t_start, t_end = group["timestamp"].min(), group["timestamp"].max()
        duration = t_end - t_start
        
        if duration <= 0: continue

        throughput = len(group[group["status"] == "OK"]) / duration

        host_cpu = {}
        for h_name, ndf in zip(host_names, node_dfs):
            if cpu_col not in ndf.columns:
                raise KeyError(f"Missing '{cpu_col}'. Available columns in {h_name}: {list(ndf.columns)}")
                
            mask = (ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end)
            avg_cpu = ndf.loc[mask, cpu_col].mean()
            host_cpu[h_name] = avg_cpu if pd.notna(avg_cpu) else 0.0

        steps.append(TelemetryStep(
            target_rps=int(rps),
            actual_throughput=throughput,
            host_cpu=host_cpu
        ))

    return Experiment(name=name, color=color, line_style=style, steps=steps)

def plot_comparison(experiments: List[Experiment], output: str = "cpu_throughput_results.pdf"):
    """Generates a 2-row plot: CPU by host (top) and Throughput (bottom)."""
    # Check if we have any data to plot after filtering
    if not any(exp.steps for exp in experiments):
        print("Warning: No data found in the specified RPS range. Plot will be empty.")
        
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    host_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']

    for i, exp in enumerate(experiments):
        if not exp.steps:
            continue
            
        df = exp.to_df()
        
        # 1. CPU Utilization (By Host)
        cpu_cols = [c for c in df.columns if c.startswith("host_cpu.")]
        for j, col in enumerate(cpu_cols):
            host_id = col.split(".")[1]
            
            color_idx = (i * len(cpu_cols) + j) % len(host_colors)
            
            axes[0].plot(df["target_rps"], df[col], 
                         exp.line_style, color=host_colors[color_idx], 
                         label=f"{exp.name} ({host_id})", alpha=0.8)

        # 2. Overall Throughput
        axes[1].plot(df["target_rps"], df["actual_throughput"], 
                     exp.line_style, color=exp.color, label=exp.name)

    axes[0].set_ylabel("CPU Utilization (%)")
    axes[0].set_title("CPU Utilization per Host")
    axes[0].grid(True, linestyle='--', alpha=0.6)
    axes[0].legend(bbox_to_anchor=(1.05, 1), loc='upper left')

    # Only plot the ideal line if we have steps
    if any(exp.steps for exp in experiments):
        max_rps = max(s.target_rps for e in experiments for s in e.steps)
        # Assuming the ideal line starts at the minimum RPS plotted to avoid a weird line drawn from 0
        min_rps = min(s.target_rps for e in experiments for s in e.steps)
        axes[1].plot([min_rps, max_rps], [min_rps, max_rps], "k:", alpha=0.5, label="Ideal")
    
    axes[1].set_ylabel("Throughput (RPS)")
    axes[1].set_xlabel("Target Load (RPS)")
    axes[1].set_title("System Throughput")
    axes[1].grid(True, linestyle='--', alpha=0.6)
    axes[1].legend(bbox_to_anchor=(1.05, 1), loc='upper left')

    plt.tight_layout()
    plt.savefig(output, format="pdf", bbox_inches="tight")
    print(f"Success: {output} generated.")

def main():
    CPU_COLUMN_NAME = "cpu_util" 
    
    # --- EASILY ADJUSTABLE LIMITS HERE ---
    MIN_RPS = 0
    MAX_RPS = 3000
    
    try:
        data = [
            parse_logs(
                name="Least Utilized", 
                color="green", 
                style="-o", 
                client_path="data/leastu/client_sift_experiment.csv", 
                server_paths=["data/leastu/h2_energy.csv", "data/leastu/h3_energy.csv"],
                cpu_col=CPU_COLUMN_NAME,
                min_rps=MIN_RPS, 
                max_rps=MAX_RPS
            )
        ]
        
        filename = f"cpu_experiment_single_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        plot_comparison(data, filename)
        
    except Exception as e:
        print(f"Pipeline Failed: {e}")

if __name__ == "__main__":
    main()