#!/usr/bin/env python3
"""
MTR to Prometheus Exporter
Runs mtr against a target and exports metrics in Prometheus format
Enhanced with multi-probe configuration support
"""

import subprocess
import json
import time
import argparse
import sys
import yaml
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


def setup_logging(log_level, log_file=None):
    """Setup logging configuration"""
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def run_mtr(target, port=None, count=10):
    """Run MTR and return parsed results"""
    cmd = ['mtr', '--report', '--report-cycles', str(count), '--json']
    
    if port:
        cmd.extend(['--port', str(port)])
    
    cmd.append(target)
    
    logging.info(f"Running MTR command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logging.error(f"MTR failed: {result.stderr}")
            return None
        
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logging.error("MTR command timed out")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse MTR JSON output: {e}")
        return None
    except Exception as e:
        logging.error(f"Error running MTR: {e}")
        return None


def format_prometheus_metrics(mtr_data, probe_name, labels_dict=None):
    """Convert MTR data to Prometheus format"""
    if not mtr_data or 'report' not in mtr_data:
        return ""
    
    report = mtr_data['report']
    hubs = report.get('hubs', [])
    
    if not hubs:
        logging.warning("No hubs found in MTR report")
        return ""
    
    timestamp = int(time.time() * 1000)
    metrics = []
    
    # Build labels from dictionary
    label_parts = []
    if labels_dict:
        for key, value in labels_dict.items():
            label_parts.append(f'{key}="{value}"')
    
    target = report.get('mtr', {}).get('dst', 'unknown')
    label_parts.append(f'target="{target}"')
    label_parts.append(f'probe="{probe_name}"')
    
    base_labels = ','.join(label_parts)
    
    # End-to-end metrics (last hop)
    last_hop = hubs[-1]
    
    metrics.append(f"# HELP mtr_end_to_end_avg_rtt_ms End-to-end average round-trip time in milliseconds")
    metrics.append(f"# TYPE mtr_end_to_end_avg_rtt_ms gauge")
    metrics.append(f"mtr_end_to_end_avg_rtt_ms{{{base_labels}}} {last_hop.get('Avg', 0)} {timestamp}")
    
    metrics.append(f"# HELP mtr_end_to_end_loss_percent End-to-end packet loss percentage")
    metrics.append(f"# TYPE mtr_end_to_end_loss_percent gauge")
    metrics.append(f"mtr_end_to_end_loss_percent{{{base_labels}}} {last_hop.get('Loss%', 0)} {timestamp}")
    
    # Calculate jitter as standard deviation
    jitter = last_hop.get('StDev', 0)
    metrics.append(f"# HELP mtr_end_to_end_jitter_ms End-to-end jitter (standard deviation) in milliseconds")
    metrics.append(f"# TYPE mtr_end_to_end_jitter_ms gauge")
    metrics.append(f"mtr_end_to_end_jitter_ms{{{base_labels}}} {jitter} {timestamp}")
    
    # Hop count
    hop_count = len(hubs)
    metrics.append(f"# HELP mtr_hop_count Number of network hops to target")
    metrics.append(f"# TYPE mtr_hop_count gauge")
    metrics.append(f"mtr_hop_count{{{base_labels}}} {hop_count} {timestamp}")
    
    # Per-hop metrics
    metrics.append(f"# HELP mtr_avg_rtt_ms Average round-trip time per hop in milliseconds")
    metrics.append(f"# TYPE mtr_avg_rtt_ms gauge")
    
    metrics.append(f"# HELP mtr_loss_percent Packet loss percentage per hop")
    metrics.append(f"# TYPE mtr_loss_percent gauge")
    
    for i, hub in enumerate(hubs, 1):
        host = hub.get('host', 'unknown')
        avg_rtt = hub.get('Avg', 0)
        loss_pct = hub.get('Loss%', 0)
        
        hop_labels = f"{base_labels},hop=\"{i}\",host=\"{host}\""
        
        metrics.append(f"mtr_avg_rtt_ms{{{hop_labels}}} {avg_rtt} {timestamp}")
        metrics.append(f"mtr_loss_percent{{{hop_labels}}} {loss_pct} {timestamp}")
    
    return "\n".join(metrics) + "\n"


def load_config(config_file):
    """Load configuration from YAML file"""
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {config_file}")
        return None
    except yaml.YAMLError as e:
        logging.error(f"Error parsing configuration file: {e}")
        return None


def run_single_probe(probe_name, target, port, labels, count, output_dir):
    """Run MTR for a single probe and return metrics"""
    logging.info(f"Running probe '{probe_name}' for {target}:{port}")
    
    # Run MTR
    mtr_data = run_mtr(target, port, count)
    if not mtr_data:
        logging.error(f"Failed to get MTR data for probe '{probe_name}'")
        return None
    
    # Format as Prometheus metrics
    prometheus_metrics = format_prometheus_metrics(mtr_data, probe_name, labels)
    if not prometheus_metrics:
        logging.error(f"Failed to format Prometheus metrics for probe '{probe_name}'")
        return None
    
    return prometheus_metrics


def run_config_mode(config_file):
    """Run multiple probes based on configuration file"""
    config = load_config(config_file)
    if not config:
        return False
    
    # Get global settings
    global_config = config.get('global', {})
    output_dir = global_config.get('output_dir', './output')
    log_level = global_config.get('log_level', 'INFO')
    log_file = global_config.get('log_file', './mtr_exporter.log')
    mtr_cycles = global_config.get('mtr_cycles', 10)
    
    # Setup logging
    setup_logging(log_level, log_file)
    
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Get probes configuration
    probes = config.get('probes', [])
    if not probes:
        logging.error("No probes defined in configuration file")
        return False
    
    all_metrics = []
    
    for probe in probes:
        probe_name = probe.get('name', 'unknown')
        target = probe.get('target')
        port = probe.get('port')
        labels = probe.get('labels', {})
        
        if not target:
            logging.warning(f"Skipping probe '{probe_name}': no target specified")
            continue
        
        metrics = run_single_probe(probe_name, target, port, labels, mtr_cycles, output_dir)
        if metrics:
            all_metrics.append(metrics)
    
    if not all_metrics:
        logging.error("No successful probe results")
        return False
    
    # Write combined metrics to file
    output_file = os.path.join(output_dir, "mtr_all_probes.prom")
    temp_file = output_file + ".tmp"
    
    try:
        with open(temp_file, 'w') as f:
            f.write('\n'.join(all_metrics))
        
        # Atomic move
        os.rename(temp_file, output_file)
        logging.info(f"Successfully wrote combined metrics to {output_file}")
        return True
    except Exception as e:
        logging.error(f"Failed to write output file: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False


def main():
    # Check if --config is in args, if so use config mode
    if '--config' in sys.argv:
        parser = argparse.ArgumentParser(description='MTR to Prometheus Exporter - Config Mode')
        parser.add_argument('--config', required=True, help='Configuration file path')
        args = parser.parse_args()
        
        if not run_config_mode(args.config):
            sys.exit(1)
        return
    
    # Otherwise use single probe mode (backwards compatible)
    parser = argparse.ArgumentParser(description='MTR to Prometheus Exporter')
    parser.add_argument('target', help='Target hostname or IP address')
    parser.add_argument('--port', type=int, help='Target port number')
    parser.add_argument('--output', required=True, help='Output file path')
    parser.add_argument('--custom-label', default='', help='Custom Prometheus labels')
    parser.add_argument('--log-level', default='INFO', help='Log level')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--count', type=int, default=10, help='Number of MTR cycles')
    
    args = parser.parse_args()
    
    setup_logging(args.log_level, args.log_file)
    
    logging.info(f"Starting MTR export for {args.target}")
    
    # Parse custom labels for backwards compatibility
    labels_dict = {}
    if args.custom_label:
        for label_pair in args.custom_label.split(','):
            if '=' in label_pair:
                key, value = label_pair.split('=', 1)
                labels_dict[key.strip()] = value.strip().strip('"')
    
    # Run MTR
    mtr_data = run_mtr(args.target, args.port, args.count)
    if not mtr_data:
        logging.error("Failed to get MTR data")
        sys.exit(1)
    
    # Format as Prometheus metrics
    prometheus_metrics = format_prometheus_metrics(mtr_data, 'default', labels_dict)
    if not prometheus_metrics:
        logging.error("Failed to format Prometheus metrics")
        sys.exit(1)
    
    # Write to output file
    try:
        with open(args.output, 'w') as f:
            f.write(prometheus_metrics)
        logging.info(f"Successfully wrote metrics to {args.output}")
    except Exception as e:
        logging.error(f"Failed to write output file: {e}")
        sys.exit(1)
    
    logging.info("MTR export completed successfully")


if __name__ == '__main__':
    main()