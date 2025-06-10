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


class MTRPrometheusExporter:
    def __init__(self, target: str, port: int = 443, count: int = 10, interval: int = 1, 
                 probe_name: str = "default", custom_labels: Optional[Dict[str, str]] = None):
        self.target = target
        self.port = port
        self.count = count
        self.interval = interval
        self.probe_name = probe_name
        self.custom_labels = custom_labels or {}
        self.timestamp = int(time.time() * 1000)  # milliseconds
        
    def run_mtr(self) -> Dict[str, Any]:
        """Run mtr command and return parsed output"""
        # Try JSON format first (note: -j conflicts with --report)
        cmd_json = [
            'mtr',
            '-j',
            '--report-cycles', str(self.count),
            '--interval', str(self.interval),
            '--port', str(self.port),
            self.target
        ]
        
        # Fallback to text format
        cmd_text = [
            'mtr',
            '--report',
            '--report-cycles', str(self.count),
            '--interval', str(self.interval),
            '--port', str(self.port),
            self.target
        ]
        
        try:
            # Try JSON format first
            print(f"Trying JSON format: {' '.join(cmd_json)}")
            result = subprocess.run(cmd_json, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                print(f"MTR returned successfully. Output length: {len(result.stdout)} chars")
                print(f"First 200 chars: {result.stdout[:200]}")
                
                try:
                    json_data = json.loads(result.stdout)
                    print("Successfully parsed JSON output")
                    return json_data
                except json.JSONDecodeError as e:
                    print(f"JSON parsing failed: {e}")
                    print("This might mean MTR was compiled without JSON support")
            else:
                print(f"JSON command failed with return code {result.returncode}")
                if result.stderr:
                    print(f"STDERR: {result.stderr}")
            
            # Fall back to text format
            print(f"Using text format: {' '.join(cmd_text)}")
            result = subprocess.run(cmd_text, capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                print(f"MTR command failed with return code {result.returncode}")
                print(f"STDERR: {result.stderr}")
                sys.exit(1)
                
            print("Successfully got text output, parsing...")
            return self.parse_mtr_text_output(result.stdout)
            
        except subprocess.TimeoutExpired:
            print("MTR command timed out")
            sys.exit(1)
        except FileNotFoundError:
            print("MTR command not found. Please install mtr package.")
            sys.exit(1)

    def parse_mtr_text_output(self, text_output: str) -> Dict[str, Any]:
        """Parse traditional MTR text output"""
        lines = text_output.strip().split('\n')
        hubs = []
        
        print(f"Parsing {len(lines)} lines of MTR output...")
        
        # Find the data lines (skip header)
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Skip header lines
            if line.startswith('Start:') or line.startswith('HOST:'):
                continue
                
            # Parse hop lines like: "  1.|-- _gateway     0.0%    10    1.6   1.6   1.6   1.8   0.1"
            if '|--' in line or ('|' in line and not line.startswith('|')):
                # Split on whitespace but be more careful
                parts = line.split()
                print(f"Parsing line: {line}")
                print(f"Split into {len(parts)} parts: {parts}")
                
                if len(parts) >= 7:
                    try:
                        # Extract hop number - it's before the first dot
                        hop_str = parts[0].split('.')[0].strip()
                        hop_num = int(hop_str)
                        
                        # Host is the second part
                        host = parts[1] if parts[1] != '???' else f"hop_{hop_num}"
                        
                        # Find the percentage - it should end with %
                        loss_pct = 0.0
                        loss_idx = -1
                        for i, part in enumerate(parts):
                            if part.endswith('%'):
                                loss_pct = float(part.rstrip('%'))
                                loss_idx = i
                                break
                        
                        if loss_idx == -1:
                            print(f"Warning: Could not find loss% in line: {line}")
                            continue
                            
                        # The numeric values should be after the loss%
                        numeric_parts = parts[loss_idx + 1:]
                        if len(numeric_parts) >= 5:
                            sent = int(numeric_parts[0])
                            last = float(numeric_parts[1])
                            avg = float(numeric_parts[2])
                            best = float(numeric_parts[3])
                            worst = float(numeric_parts[4])
                            stddev = float(numeric_parts[5]) if len(numeric_parts) > 5 else 0.0
                        else:
                            print(f"Warning: Not enough numeric values in line: {line}")
                            continue
                        
                        hub = {
                            'count': hop_num,
                            'host': host,
                            'Loss%': loss_pct,
                            'Snt': sent,
                            'Last': last,
                            'Avg': avg,
                            'Best': best,
                            'Wrst': worst,
                            'StDev': stddev
                        }
                        hubs.append(hub)
                        print(f"Successfully parsed hop {hop_num}: {host}")
                        
                    except (ValueError, IndexError) as e:
                        print(f"Warning: Could not parse line: {line} - {e}")
                        continue
        
        print(f"Successfully parsed {len(hubs)} hops")
        return {'report': {'hubs': hubs}}

    def parse_mtr_data(self, mtr_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse MTR JSON data and extract metrics"""
        hops = []
        
        if 'report' not in mtr_data or 'hubs' not in mtr_data['report']:
            print("Invalid MTR data structure")
            return []
            
        for hub in mtr_data['report']['hubs']:
            hop_data = {
                'hop': hub.get('count', 0),
                'host': hub.get('host', 'unknown'),
                'loss_percent': hub.get('Loss%', 0.0),
                'sent': hub.get('Snt', 0),
                'last_ms': hub.get('Last', 0.0),
                'avg_ms': hub.get('Avg', 0.0),
                'best_ms': hub.get('Best', 0.0),
                'worst_ms': hub.get('Wrst', 0.0),
                'stddev_ms': hub.get('StDev', 0.0),  # This is our jitter metric
            }
            hops.append(hop_data)
            
        return hops

    def calculate_path_health_summary(self, hops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate path health summary metrics"""
        if not hops:
            return {}
        
        # Filter out hops with 100% loss (like ??? hops in github path)
        valid_hops = [hop for hop in hops if hop['loss_percent'] < 100.0]
        
        if not valid_hops:
            return {}
        
        # Total path metrics
        total_loss = max(hop['loss_percent'] for hop in hops)  # Use max loss along path
        avg_loss = sum(hop['loss_percent'] for hop in valid_hops) / len(valid_hops)
        
        # Round trip delay (end-to-end)
        end_to_end_rtt = hops[-1]['avg_ms'] if hops else 0.0
        
        # Round trip jitter (end-to-end)
        end_to_end_jitter = hops[-1]['stddev_ms'] if hops else 0.0
        
        # Path stability (based on jitter across all hops)
        avg_jitter = sum(hop['stddev_ms'] for hop in valid_hops) / len(valid_hops)
        max_jitter = max(hop['stddev_ms'] for hop in valid_hops)
        
        # Path consistency (RTT variance across hops)
        rtts = [hop['avg_ms'] for hop in valid_hops]
        rtt_variance = max(rtts) - min(rtts) if len(rtts) > 1 else 0.0
        
        # Health score calculation (0-100, higher is better)
        loss_penalty = total_loss * 2  # 2 points per 1% loss
        jitter_penalty = min(end_to_end_jitter * 0.5, 20)  # Max 20 points for jitter
        rtt_penalty = min(end_to_end_rtt * 0.1, 20)  # Max 20 points for high RTT
        variance_penalty = min(rtt_variance * 0.05, 10)  # Max 10 points for variance
        
        health_score = max(0, 100 - loss_penalty - jitter_penalty - rtt_penalty - variance_penalty)
        
        # Determine health status
        if health_score >= 90:
            health_status = "EXCELLENT"
        elif health_score >= 75:
            health_status = "GOOD"
        elif health_score >= 60:
            health_status = "FAIR"
        elif health_score >= 40:
            health_status = "POOR"
        else:
            health_status = "CRITICAL"
        
        return {
            'hop_count': len(hops),
            'valid_hops': len(valid_hops),
            'total_loss_percent': total_loss,
            'avg_loss_percent': avg_loss,
            'end_to_end_rtt_ms': end_to_end_rtt,
            'end_to_end_jitter_ms': end_to_end_jitter,
            'avg_jitter_ms': avg_jitter,
            'max_jitter_ms': max_jitter,
            'rtt_variance_ms': rtt_variance,
            'health_score': round(health_score, 1),
            'health_status': health_status
        }

    def build_labels(self, extra_labels: Optional[Dict[str, str]] = None) -> str:
        """Build label string for Prometheus metrics"""
        labels = {}
        
        # Add custom labels
        labels.update(self.custom_labels)
        
        # Add target and probe info
        labels['target'] = self.target
        labels['probe'] = self.probe_name
        
        # Add any extra labels
        if extra_labels:
            labels.update(extra_labels)
        
        # Format as Prometheus labels
        label_parts = [f'{key}="{value}"' for key, value in labels.items()]
        return ','.join(label_parts)

    def generate_prometheus_metrics(self, hops: List[Dict[str, Any]]) -> str:
        """Generate Prometheus format metrics"""
        metrics = []
        
        # Base labels for metrics
        base_labels = self.build_labels()
        
        # Calculate path health summary
        summary = self.calculate_path_health_summary(hops)
        
        # Add metadata
        metrics.append(f"# HELP mtr_info MTR trace information")
        metrics.append(f"# TYPE mtr_info gauge")
        metrics.append(f'mtr_info{{{base_labels},port="{self.port}"}} 1')
        metrics.append("")
        
        # Path Health Summary Metrics
        if summary:
            metrics.append("# HELP mtr_path_health_score Overall path health score (0-100, higher is better)")
            metrics.append("# TYPE mtr_path_health_score gauge")
            metrics.append(f'mtr_path_health_score{{{base_labels}}} {summary["health_score"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_path_rtt_variance_ms RTT variance across the path")
            metrics.append("# TYPE mtr_path_rtt_variance_ms gauge")
            metrics.append(f'mtr_path_rtt_variance_ms{{{base_labels}}} {summary["rtt_variance_ms"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_path_avg_jitter_ms Average jitter across all valid hops")
            metrics.append("# TYPE mtr_path_avg_jitter_ms gauge")
            metrics.append(f'mtr_path_avg_jitter_ms{{{base_labels}}} {summary["avg_jitter_ms"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_path_max_jitter_ms Maximum jitter across all valid hops")
            metrics.append("# TYPE mtr_path_max_jitter_ms gauge")
            metrics.append(f'mtr_path_max_jitter_ms{{{base_labels}}} {summary["max_jitter_ms"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_path_total_loss_percent Maximum packet loss percentage along the path")
            metrics.append("# TYPE mtr_path_total_loss_percent gauge")
            metrics.append(f'mtr_path_total_loss_percent{{{base_labels}}} {summary["total_loss_percent"]}')
            metrics.append("")
        
        # Packet loss percentage per hop
        metrics.append("# HELP mtr_loss_percent Packet loss percentage per hop")
        metrics.append("# TYPE mtr_loss_percent gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_loss_percent{{{hop_labels}}} {hop["loss_percent"]}')
        metrics.append("")
        
        # Packets sent per hop
        metrics.append("# HELP mtr_packets_sent Total packets sent per hop")
        metrics.append("# TYPE mtr_packets_sent counter")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_packets_sent{{{hop_labels}}} {hop["sent"]}')
        metrics.append("")
        
        # Last round trip time
        metrics.append("# HELP mtr_last_rtt_ms Last round trip time in milliseconds")
        metrics.append("# TYPE mtr_last_rtt_ms gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_last_rtt_ms{{{hop_labels}}} {hop["last_ms"]}')
        metrics.append("")
        
        # Average round trip time
        metrics.append("# HELP mtr_avg_rtt_ms Average round trip time in milliseconds")
        metrics.append("# TYPE mtr_avg_rtt_ms gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_avg_rtt_ms{{{hop_labels}}} {hop["avg_ms"]}')
        metrics.append("")
        
        # Best round trip time
        metrics.append("# HELP mtr_best_rtt_ms Best round trip time in milliseconds")
        metrics.append("# TYPE mtr_best_rtt_ms gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_best_rtt_ms{{{hop_labels}}} {hop["best_ms"]}')
        metrics.append("")
        
        # Worst round trip time
        metrics.append("# HELP mtr_worst_rtt_ms Worst round trip time in milliseconds")
        metrics.append("# TYPE mtr_worst_rtt_ms gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_worst_rtt_ms{{{hop_labels}}} {hop["worst_ms"]}')
        metrics.append("")
        
        # Jitter (standard deviation)
        metrics.append("# HELP mtr_jitter_ms Jitter (standard deviation) in milliseconds")
        metrics.append("# TYPE mtr_jitter_ms gauge")
        for hop in hops:
            hop_labels = self.build_labels({'hop': str(hop['hop']), 'host': hop['host']})
            metrics.append(f'mtr_jitter_ms{{{hop_labels}}} {hop["stddev_ms"]}')
        metrics.append("")
        
        # Total hop count
        metrics.append("# HELP mtr_hop_count Total number of hops to target")
        metrics.append("# TYPE mtr_hop_count gauge")
        if hops:
            metrics.append(f'mtr_hop_count{{{base_labels}}} {len(hops)}')
        metrics.append("")
        
        # End-to-end metrics (using last hop)
        if hops:
            last_hop = hops[-1]
            metrics.append("# HELP mtr_end_to_end_loss_percent End-to-end packet loss percentage")
            metrics.append("# TYPE mtr_end_to_end_loss_percent gauge")
            metrics.append(f'mtr_end_to_end_loss_percent{{{base_labels}}} {last_hop["loss_percent"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_end_to_end_avg_rtt_ms End-to-end average round trip time")
            metrics.append("# TYPE mtr_end_to_end_avg_rtt_ms gauge")
            metrics.append(f'mtr_end_to_end_avg_rtt_ms{{{base_labels}}} {last_hop["avg_ms"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_end_to_end_jitter_ms End-to-end jitter")
            metrics.append("# TYPE mtr_end_to_end_jitter_ms gauge")
            metrics.append(f'mtr_end_to_end_jitter_ms{{{base_labels}}} {last_hop["stddev_ms"]}')
            metrics.append("")
        
        # Timestamp
        metrics.append("# HELP mtr_last_run_timestamp_ms Timestamp of last MTR run")
        metrics.append("# TYPE mtr_last_run_timestamp_ms gauge")
        metrics.append(f'mtr_last_run_timestamp_ms{{{base_labels}}} {self.timestamp}')
        
        return "\n".join(metrics)

    def export_to_file(self, output_file: str):
        """Run MTR and export metrics to file"""
        print(f"Running MTR to {self.target}:{self.port} (probe: {self.probe_name})...")
        mtr_data = self.run_mtr()
        
        print("Parsing MTR data...")
        hops = self.parse_mtr_data(mtr_data)
        
        if not hops:
            print("No hop data found")
            sys.exit(1)
            
        print(f"Found {len(hops)} hops")
        
        print("Generating Prometheus metrics...")
        prometheus_metrics = self.generate_prometheus_metrics(hops)
        
        print(f"Writing metrics to {output_file}...")
        with open(output_file, 'w') as f:
            f.write(prometheus_metrics)
            
        print(f"Successfully exported metrics to {output_file}")
        
        # Print path health summary
        summary = self.calculate_path_health_summary(hops)
        if summary:
            print(f"\n=== PATH HEALTH SUMMARY for {self.probe_name} ===")
            print(f"Status: {summary['health_status']} (Score: {summary['health_score']}/100)")
            print(f"Round Trip Delay: {summary['end_to_end_rtt_ms']:.2f}ms")
            print(f"Round Trip Jitter: {summary['end_to_end_jitter_ms']:.2f}ms")
            print(f"Path Loss: {summary['total_loss_percent']:.1f}%")
            print(f"Hop Count: {summary['hop_count']} ({summary['valid_hops']} valid)")
            print(f"RTT Variance: {summary['rtt_variance_ms']:.2f}ms")
            print(f"Avg Path Jitter: {summary['avg_jitter_ms']:.2f}ms")
        
        # Print detailed per-hop summary
        print(f"\n=== DETAILED HOP ANALYSIS for {self.probe_name} ===")
        for hop in hops:
            status = "❌" if hop['loss_percent'] == 100.0 else "⚠️" if hop['loss_percent'] > 0 else "✅"
            print(f"  {status} Hop {hop['hop']:2d}: {hop['host']:30s} "
                  f"Loss: {hop['loss_percent']:5.1f}% "
                  f"Avg: {hop['avg_ms']:7.2f}ms "
                  f"Jitter: {hop['stddev_ms']:6.2f}ms")


def load_config(config_file: str) -> Dict[str, Any]:
    """Load configuration from YAML file"""
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing configuration file: {e}")
        sys.exit(1)


def run_config_mode(config_file: str):
    """Run multiple probes based on configuration file"""
    config = load_config(config_file)
    
    # Get global settings
    global_config = config.get('global', {})
    output_dir = global_config.get('output_dir', './output')
    mtr_cycles = global_config.get('mtr_cycles', 10)
    
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Get probes configuration
    probes = config.get('probes', [])
    if not probes:
        print("No probes defined in configuration file")
        sys.exit(1)
    
    all_metrics = []
    
    for probe_config in probes:
        probe_name = probe_config.get('name', 'unknown')
        target = probe_config.get('target')
        port = probe_config.get('port', 443)
        labels = probe_config.get('labels', {})
        
        if not target:
            print(f"Skipping probe '{probe_name}': no target specified")
            continue
        
        print(f"\n=== Running probe: {probe_name} ===")
        
        exporter = MTRPrometheusExporter(
            target=target,
            port=port,
            count=mtr_cycles,
            probe_name=probe_name,
            custom_labels=labels
        )
        
        # Generate metrics
        mtr_data = exporter.run_mtr()
        hops = exporter.parse_mtr_data(mtr_data)
        
        if hops:
            metrics = exporter.generate_prometheus_metrics(hops)
            all_metrics.append(metrics)
            
            # Print path health summary
            summary = exporter.calculate_path_health_summary(hops)
            if summary:
                print(f"\n=== PATH HEALTH SUMMARY for {probe_name} ===")
                print(f"Status: {summary['health_status']} (Score: {summary['health_score']}/100)")
                print(f"Round Trip Delay: {summary['end_to_end_rtt_ms']:.2f}ms")
                print(f"Round Trip Jitter: {summary['end_to_end_jitter_ms']:.2f}ms")
                print(f"Path Loss: {summary['total_loss_percent']:.1f}%")
                print(f"Hop Count: {summary['hop_count']} ({summary['valid_hops']} valid)")
                print(f"RTT Variance: {summary['rtt_variance_ms']:.2f}ms")
            
            # Print condensed per-hop summary
            print(f"\nDetailed hops for {probe_name}:")
            for hop in hops:
                status = "❌" if hop['loss_percent'] == 100.0 else "⚠️" if hop['loss_percent'] > 0 else "✅"
                print(f"  {status} Hop {hop['hop']:2d}: {hop['host']:30s} "
                      f"Loss: {hop['loss_percent']:5.1f}% "
                      f"Avg: {hop['avg_ms']:7.2f}ms "
                      f"Jitter: {hop['stddev_ms']:6.2f}ms")
        else:
            print(f"❌ No hop data found for probe '{probe_name}'")
    
    if not all_metrics:
        print("No successful probe results")
        sys.exit(1)
    
    # Write combined metrics to file
    output_file = os.path.join(output_dir, "mtr_all_probes.prom")
    temp_file = output_file + ".tmp"
    
    try:
        with open(temp_file, 'w') as f:
            f.write('\n\n'.join(all_metrics))
        
        # Atomic move
        os.rename(temp_file, output_file)
        print(f"\n=== Successfully wrote combined metrics to {output_file} ===")
    except Exception as e:
        print(f"Failed to write output file: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        sys.exit(1)


def main():
    # Check if --config is in args, if so use config mode
    if '--config' in sys.argv:
        parser = argparse.ArgumentParser(description='MTR to Prometheus Exporter - Config Mode')
        parser.add_argument('--config', required=True, help='Configuration file path')
        args = parser.parse_args()
        
        run_config_mode(args.config)
        return
    
    # Otherwise use single probe mode (backwards compatible)
    parser = argparse.ArgumentParser(description='Export MTR metrics to Prometheus format')
    parser.add_argument('target', help='Target hostname or IP address')
    parser.add_argument('-p', '--port', type=int, default=443, help='Target port (default: 443)')
    parser.add_argument('-c', '--count', type=int, default=10, help='Number of pings per hop (default: 10)')
    parser.add_argument('-i', '--interval', type=int, default=1, help='Interval between pings in seconds (default: 1)')
    parser.add_argument('-o', '--output', default='mtr_metrics.prom', help='Output file (default: mtr_metrics.prom)')
    parser.add_argument('--probe-name', default='default', help='Probe name for metrics (default: default)')
    parser.add_argument('--label', action='append', help='Add custom label in key=value format')
    
    args = parser.parse_args()
    
    # Parse custom labels
    custom_labels = {}
    if args.label:
        for label in args.label:
            if '=' in label:
                key, value = label.split('=', 1)
                custom_labels[key.strip()] = value.strip()
    
    exporter = MTRPrometheusExporter(
        target=args.target,
        port=args.port,
        count=args.count,
        interval=args.interval,
        probe_name=args.probe_name,
        custom_labels=custom_labels
    )
    
    exporter.export_to_file(args.output)


if __name__ == '__main__':
    main()