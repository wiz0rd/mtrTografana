#!/usr/bin/env python3
"""
MTR to Prometheus Exporter
Runs mtr against a target and exports metrics in Prometheus format
"""

import subprocess
import json
import time
import argparse
import sys
from datetime import datetime
from typing import Dict, List, Any

class MTRPrometheusExporter:
    def __init__(self, target: str, port: int = 443, count: int = 10, interval: int = 1):
        self.target = target
        self.port = port
        self.count = count
        self.interval = interval
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

    def generate_prometheus_metrics(self, hops: List[Dict[str, Any]]) -> str:
        """Generate Prometheus format metrics"""
        metrics = []
        
        # Add metadata
        metrics.append(f"# HELP mtr_info MTR trace information")
        metrics.append(f"# TYPE mtr_info gauge")
        metrics.append(f'mtr_info{{target="{self.target}",port="{self.port}"}} 1')
        metrics.append("")
        
        # Packet loss percentage per hop
        metrics.append("# HELP mtr_loss_percent Packet loss percentage per hop")
        metrics.append("# TYPE mtr_loss_percent gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_loss_percent{{{labels}}} {hop["loss_percent"]}')
        metrics.append("")
        
        # Packets sent per hop
        metrics.append("# HELP mtr_packets_sent Total packets sent per hop")
        metrics.append("# TYPE mtr_packets_sent counter")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_packets_sent{{{labels}}} {hop["sent"]}')
        metrics.append("")
        
        # Last round trip time
        metrics.append("# HELP mtr_last_rtt_ms Last round trip time in milliseconds")
        metrics.append("# TYPE mtr_last_rtt_ms gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_last_rtt_ms{{{labels}}} {hop["last_ms"]}')
        metrics.append("")
        
        # Average round trip time
        metrics.append("# HELP mtr_avg_rtt_ms Average round trip time in milliseconds")
        metrics.append("# TYPE mtr_avg_rtt_ms gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_avg_rtt_ms{{{labels}}} {hop["avg_ms"]}')
        metrics.append("")
        
        # Best round trip time
        metrics.append("# HELP mtr_best_rtt_ms Best round trip time in milliseconds")
        metrics.append("# TYPE mtr_best_rtt_ms gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_best_rtt_ms{{{labels}}} {hop["best_ms"]}')
        metrics.append("")
        
        # Worst round trip time
        metrics.append("# HELP mtr_worst_rtt_ms Worst round trip time in milliseconds")
        metrics.append("# TYPE mtr_worst_rtt_ms gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_worst_rtt_ms{{{labels}}} {hop["worst_ms"]}')
        metrics.append("")
        
        # Jitter (standard deviation)
        metrics.append("# HELP mtr_jitter_ms Jitter (standard deviation) in milliseconds")
        metrics.append("# TYPE mtr_jitter_ms gauge")
        for hop in hops:
            labels = f'hop="{hop["hop"]}",host="{hop["host"]}",target="{self.target}"'
            metrics.append(f'mtr_jitter_ms{{{labels}}} {hop["stddev_ms"]}')
        metrics.append("")
        
        # Total hop count
        metrics.append("# HELP mtr_hop_count Total number of hops to target")
        metrics.append("# TYPE mtr_hop_count gauge")
        if hops:
            metrics.append(f'mtr_hop_count{{target="{self.target}"}} {len(hops)}')
        metrics.append("")
        
        # End-to-end metrics (using last hop)
        if hops:
            last_hop = hops[-1]
            metrics.append("# HELP mtr_end_to_end_loss_percent End-to-end packet loss percentage")
            metrics.append("# TYPE mtr_end_to_end_loss_percent gauge")
            metrics.append(f'mtr_end_to_end_loss_percent{{target="{self.target}"}} {last_hop["loss_percent"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_end_to_end_avg_rtt_ms End-to-end average round trip time")
            metrics.append("# TYPE mtr_end_to_end_avg_rtt_ms gauge")
            metrics.append(f'mtr_end_to_end_avg_rtt_ms{{target="{self.target}"}} {last_hop["avg_ms"]}')
            metrics.append("")
            
            metrics.append("# HELP mtr_end_to_end_jitter_ms End-to-end jitter")
            metrics.append("# TYPE mtr_end_to_end_jitter_ms gauge")
            metrics.append(f'mtr_end_to_end_jitter_ms{{target="{self.target}"}} {last_hop["stddev_ms"]}')
            metrics.append("")
        
        # Timestamp
        metrics.append("# HELP mtr_last_run_timestamp_ms Timestamp of last MTR run")
        metrics.append("# TYPE mtr_last_run_timestamp_ms gauge")
        metrics.append(f'mtr_last_run_timestamp_ms{{target="{self.target}"}} {self.timestamp}')
        
        return "\n".join(metrics)

    def export_to_file(self, output_file: str):
        """Run MTR and export metrics to file"""
        print(f"Running MTR to {self.target}:{self.port}...")
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
        
        # Print summary
        print("\nSummary:")
        for hop in hops:
            print(f"  Hop {hop['hop']:2d}: {hop['host']:30s} "
                  f"Loss: {hop['loss_percent']:5.1f}% "
                  f"Avg: {hop['avg_ms']:7.2f}ms "
                  f"Jitter: {hop['stddev_ms']:6.2f}ms")

def main():
    parser = argparse.ArgumentParser(description='Export MTR metrics to Prometheus format')
    parser.add_argument('target', help='Target hostname or IP address')
    parser.add_argument('-p', '--port', type=int, default=443, help='Target port (default: 443)')
    parser.add_argument('-c', '--count', type=int, default=10, help='Number of pings per hop (default: 10)')
    parser.add_argument('-i', '--interval', type=int, default=1, help='Interval between pings in seconds (default: 1)')
    parser.add_argument('-o', '--output', default='mtr_metrics.prom', help='Output file (default: mtr_metrics.prom)')
    
    args = parser.parse_args()
    
    exporter = MTRPrometheusExporter(
        target=args.target,
        port=args.port,
        count=args.count,
        interval=args.interval
    )
    
    exporter.export_to_file(args.output)

if __name__ == '__main__':
    main()
