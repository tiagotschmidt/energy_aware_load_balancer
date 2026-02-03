# no-name-project: Energy-Aware In-Network Load Balancer

[cite_start] no-name-project is an in-network energy-efficiency-based load balancer implemented using P4. [cite_start]It dynamically distributes network traffic based on real-time energy metrics collected from backend servers to minimize total power consumption without compromising performance.

## Project Structure

- [cite_start]`p4src/`: Contains the P4-16 source code for the programmable data plane.
- [cite_start]`server_agent.py`: A lightweight agent that runs on backend servers to monitor CPU utilization and power consumption (via Intel RAPL).
- [cite_start]`topology.json`: Defines the Mininet network topology, including the programmable switch, client, and backend servers.
- [cite_start]`p4-utils/`: Infrastructure and utility scripts for compiling P4 code and running Mininet experiments[cite: 1, 6].
- [cite_start]`Makefile`: Automates the build process, including P4 compilation and environment setup.

## Prerequisites

- [cite_start]P4-16 compiler (`p4c`).
- [cite_start]BMv2 behavioral model switch[cite: 1, 6].
- [cite_start]Mininet[cite: 6].
- Python 3 with `socket` and `os` modules for the server agents.


