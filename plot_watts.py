# /// script
# dependencies = [
#   "pandas",
#   "matplotlib",
#   "pydantic",
#   "mplcursors"
# ]
# ///

import pandas as pd
import matplotlib.pyplot as plt
import mplcursors
from pydantic import BaseModel
from typing import List


class TelemetryStep(BaseModel):
    """Tracks time and energy state sequentially."""
    elapsed_time_s: float
    accumulated_energy_j: float

class Experiment(BaseModel):
    name: str
    color: str
    line_style: str
    steps: List[TelemetryStep]

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([s.model_dump() for s in self.steps])

# --- The Parser ---
def parse_logs(name: str, color: str, style: str, client_path: str, server_paths: List[str]) -> Experiment:
    try:
        df_c = pd.read_csv(client_path)
        node_dfs = [pd.read_csv(p) for p in server_paths]
    except Exception as e:
        raise ValueError(f"IO Error in {name}: {e}")

    # Sort groups by timestamp to ensure chronological order for time accumulation
    grouped = [(rps, group) for rps, group in df_c.groupby("target_rate")]
    grouped.sort(key=lambda x: x[1]["timestamp"].min())

    steps = []
    cumulative_time = 0.0
    cumulative_energy = 0.0

    for rps, group in grouped:
        t_start = group["timestamp"].min()
        t_end = group["timestamp"].max()
        duration = t_end - t_start
        
        if duration <= 0: continue

        # Average power across all nodes during this timeframe
        total_power = sum(
            ndf.loc[(ndf["timestamp"] >= t_start) & (ndf["timestamp"] <= t_end), "power_watts"].mean() or 0.0
            for ndf in node_dfs
        )

        # Calculate Joules (Watts * Seconds) and accumulate
        step_energy_j = total_power * duration
        cumulative_time += duration
        cumulative_energy += step_energy_j

        steps.append(TelemetryStep(
            elapsed_time_s=cumulative_time,
            accumulated_energy_j=cumulative_energy
        ))

    return Experiment(name=name, color=color, line_style=style, steps=steps)

# --- The Plotter ---
def plot_energy_comparison(experiments: List[Experiment], output: str = "accumulated_energy.pdf"):
    fig, ax = plt.subplots(figsize=(10, 6))

    final_energies = {}
    lines = []

    for exp in experiments:
        df = exp.to_df()
        
        # Prepend origin (0,0) to ensure the area plot anchors to the start
        times = [0.0] + df["elapsed_time_s"].tolist()
        energies = [0.0] + df["accumulated_energy_j"].tolist()
        
        line, = ax.plot(times, energies, exp.line_style, color=exp.color, label=exp.name)
        lines.append(line)
        
        # Add the overlapping green/red area
        ax.fill_between(times, energies, color=exp.color, alpha=0.2)
        
        # Store final accumulated energy for calculations
        final_energies[exp.name] = energies[-1]

    # Calculate overall energy saved percentage
    rr_energy = final_energies.get("Round Robin Only", 0)
    ea_energy = final_energies.get("Energy Aware", 0)
    
    if rr_energy > 0 and ea_energy > 0:
        savings_pct = ((rr_energy - ea_energy) / rr_energy) * 100
        
        # Add a static "hover" square (annotation box) for the exported PDF
        textstr = f"Overall Energy Saved: {savings_pct:.2f}%"
        props = dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.9, edgecolor='black')
        ax.text(0.05, 0.90, textstr, transform=ax.transAxes, fontsize=11,
                verticalalignment='top', bbox=props, zorder=10)

    ax.set_xlabel("Time (seconds from start)")
    ax.set_ylabel("Accumulated Energy (Joules)")
    ax.set_title("Accumulated Energy Consumption Over Time")
    ax.grid(True, linestyle='--', alpha=0.6)
    ax.legend()

    # Enable interactive hover tooltips for GUI viewing
    cursor = mplcursors.cursor(lines, hover=True)
    @cursor.connect("add")
    def on_add(sel):
        x, y = sel.target
        sel.annotation.set_text(f"{sel.artist.get_label()}\nTime: {x:.1f}s\nEnergy: {y:.1f} J")
        sel.annotation.get_bbox_patch().set(facecolor="white", alpha=0.9)

    plt.tight_layout()
    plt.savefig(output, format="pdf", bbox_inches="tight")
    print(f"Success: {output} generated.")
    
    # plt.show() # Uncomment this to see the interactive hover tooltips in a window

def main():
    try:
        data = [
            parse_logs("Round Robin", "red", "-", 
                       "data/roundrobin/client_sift_experiment.csv", ["data/roundrobin/h2_energy.csv", "data/roundrobin/h3_energy.csv"]),
            parse_logs("Energy Aware", "green", "-", 
                       "data/marginal/client_sift_experiment.csv", ["data/marginal/h2_energy.csv", "data/marginal/h3_energy.csv"])
        ]
        filename = f"accumulated_energy_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        plot_energy_comparison(data, filename)
    except Exception as e:
        print(f"Pipeline Failed: {e}")

if __name__ == "__main__":
    main()