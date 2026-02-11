import pandas as pd
import matplotlib.pyplot as plt

def load_and_process(mode_name, client_csv, h2_csv, h3_csv):
    """
    Reads logs and aggregates them by 'target_rate' (RPS Steps).
    """
    # Load Data
    try:
        df_c = pd.read_csv(client_csv)
        df_h2 = pd.read_csv(h2_csv)
        df_h3 = pd.read_csv(h3_csv)
    except FileNotFoundError as e:
        print(f"Error loading {mode_name}: {e}")
        return None

    results = []
    
    # Group client data by the Step (Target RPS)
    for rps, group in df_c.groupby('target_rate'):
        start_t = group['timestamp'].min()
        end_t = group['timestamp'].max()
        duration = end_t - start_t
        
        if duration <= 0: continue

        # 1. Latency (Accumulated/Avg P99 for this step)
        success = group[group['status'] == 'OK']
        p99 = success['latency_ms'].quantile(0.99) if not success.empty else 0
        
        # 2. Throughput (Real RPS vs Target RPS)
        real_throughput = len(success) / duration

        # 3. Power (Sum of H2 + H3 Avg Watts during this step)
        # Filter server logs to this specific time window
        h2_step = df_h2[(df_h2['timestamp'] >= start_t) & (df_h2['timestamp'] <= end_t)]
        h3_step = df_h3[(df_h3['timestamp'] >= start_t) & (df_h3['timestamp'] <= end_t)]
        
        avg_watts_h2 = h2_step['power_watts'].mean() if not h2_step.empty else 0
        avg_watts_h3 = h3_step['power_watts'].mean() if not h3_step.empty else 0
        
        total_cluster_power = avg_watts_h2 + avg_watts_h3

        results.append({
            'rps': rps,
            'p99': p99,
            'throughput': real_throughput,
            'power': total_cluster_power
        })

    return pd.DataFrame(results)

def plot_final():
    # You must rename your logs after each run to match these names!
    df_perf = load_and_process("Performance", "logs/client_perf.csv", "logs/h2_perf.csv", "logs/h3_perf.csv")
    df_energy = load_and_process("Energy-Aware", "logs/client_energy.csv", "logs/h2_energy.csv", "logs/h3_energy.csv")

    if df_perf is None or df_energy is None: return

    fig, axes = plt.subplots(3, 1, figsize=(8, 12), sharex=True)

    # Plot 1: Latency vs RPS
    axes[0].plot(df_perf['rps'], df_perf['p99'], 'r--s', label='Performance Only')
    axes[0].plot(df_energy['rps'], df_energy['p99'], 'g-o', label='Energy Aware')
    axes[0].set_ylabel('P99 Latency (ms)')
    axes[0].set_title('1. Latency (Lower is Better)')
    axes[0].legend()
    axes[0].grid(True)

    # Plot 2: Total Power vs RPS
    axes[1].plot(df_perf['rps'], df_perf['power'], 'r--s', label='Performance Only')
    axes[1].plot(df_energy['rps'], df_energy['power'], 'g-o', label='Energy Aware')
    axes[1].set_ylabel('Cluster Power (Watts)')
    axes[1].set_title('2. Total Power Consumption (Lower is Better)')
    axes[1].legend()
    axes[1].grid(True)

    # Plot 3: Throughput vs RPS
    axes[2].plot(df_perf['rps'], df_perf['throughput'], 'r--s', label='Performance Only')
    axes[2].plot(df_energy['rps'], df_energy['throughput'], 'g-o', label='Energy Aware')
    axes[2].plot(df_energy['rps'], df_energy['rps'], 'k:', alpha=0.5, label='Ideal Target')
    axes[2].set_ylabel('Throughput (Req/s)')
    axes[2].set_xlabel('Target Load (RPS)')
    axes[2].set_title('3. Throughput (Higher is Better)')
    axes[2].legend()
    axes[2].grid(True)

    plt.tight_layout()
    plt.savefig("final_comparison_rps.png")
    print("Graph saved: final_comparison_rps.png")
    plt.show()

if __name__ == "__main__":
    plot_final()