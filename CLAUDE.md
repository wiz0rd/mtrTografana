# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an MTR (My Traceroute) to Grafana monitoring system that exports network performance metrics to Prometheus for visualization in Grafana dashboards. The system performs network path analysis and exports metrics in Prometheus format.

## Architecture

- **mtr_metrics.py**: Main automation script (bash script with .py extension) that orchestrates MTR data collection
- **grafanaDashboardTemplate.json**: Pre-configured Grafana dashboard for visualizing MTR network performance metrics
- **Missing dependency**: References `mtr_exporter.py` Python script (not present in repo) that handles the actual MTR data processing

## Key Components

The system generates these Prometheus metrics:
- `mtr_end_to_end_avg_rtt_ms`: End-to-end average round-trip time
- `mtr_end_to_end_loss_percent`: Packet loss percentage
- `mtr_end_to_end_jitter_ms`: Network jitter measurements
- `mtr_hop_count`: Number of network hops
- `mtr_avg_rtt_ms`: Per-hop latency data
- `mtr_loss_percent`: Per-hop packet loss data

## Configuration

Default configuration in mtr_metrics.py:
- Target: `eur.ra.army.mil:443`
- Output directory: `/var/lib/prometheus/textfile_collector`
- Log file: `/var/log/mtr_exporter.log`
- Custom labels: `environment="production",service="army_mil"`

## Dependencies

- `mtr` command-line tool must be installed
- Python 3 with the referenced `mtr_exporter.py` script
- Prometheus with textfile collector enabled
- Grafana with Prometheus datasource configured

## Dashboard Features

The Grafana dashboard includes:
- End-to-end latency, packet loss, jitter, and hop count stat panels
- Time series graphs for latency trends and packet loss over time
- Per-hop latency and loss distribution charts
- Network path table showing detailed hop information

## Running the System

Execute the main script: `./mtr_metrics.py [--verbose]`

The script handles:
- Directory creation and permission management
- Atomic file writes using temporary files
- Comprehensive logging and error handling
- Integration with cron/systemd for continuous monitoring