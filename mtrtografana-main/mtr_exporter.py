#!/usr/bin/env python3
"""
MTR Prometheus Exporter - Unified Script
Supports both single probe mode and multi-probe config mode
Fixed version with proper Prometheus formatting and newline handling
"""

import argparse
import sys
import os
import time
import subprocess
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    import yaml
except ImportError:
    print("PyYAML not found. Install with: pip3 install PyYAML")
    print("Config mode will not work without PyYAML.")
    yaml = None


class MTRPrometheusExporter:
    def __init__(self, target: str, port: int = 443, count: int = 10, interval: int = 1, 
                 probe_name: str = "default", custom_labels: Optional[Dict[str, str]] = None,
                 protocol: str = "icmp"):
        self.target = target
        self.port = port
        self.count = count
        self.interval = interval
        self.probe_name = probe_name
        self.custom_labels = custom_labels or {}
        self.protocol = protocol.lower()
        self.timestamp = int(time.time() * 1000)  # milliseconds
        
    def run_mtr(self) -> Dict[str, Any]:
        """Run mtr command and return parsed output"""
        
        # Build base command
        base_cmd = [
            'mtr',
            '--report-cycles', str(self.count),
            '--interval', str(self.interval),
        ]
        
        # Add protocol-specific flags
        if self.protocol == 'tcp':
            base_cmd.append('--tcp')
            base_cmd.extend(['--port', str(self.port)])
        elif self.protocol == 'udp':
            base_cmd.append('--udp')
            base_cmd.extend(['--port', str(self.port)])
        elif self.protocol == 'icmp':
            # ICMP is default, no extra flags needed
            # Port is not used with ICMP
            pass
        else:
            print(f"Warning: Unknown protocol '{self.protocol}', defaulting to ICMP")
        
        base_cmd.append(self.target)
        
        # Try JSON format first
        cmd_json = base_cmd.copy()
        cmd_json.insert(1, '-j')  # Insert JSON flag after 'mtr'
        
        # Fallback to text format
        cmd_text = base_cmd.copy()
        cmd_text.insert(1, '--report')  # Insert report flag after 'mtr'
        
        try:
            # Try JSON format first
            print(f"Trying JSON format: {' '.join(cmd_json)}")
            print(f"Protocol: {self.protocol.upper()}, Port: {self.port if self.protocol in ['tcp', 'udp'] else 'N/A (ICMP)'}")
            
            result = subprocess.run(cmd_json, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                  universal_newlines=True, timeout=120)
            
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
            result = subprocess.run(cmd_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                  universal_newlines=True, timeout=120)
            
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
                        try:
                            hop_num = int(hop_str)
                        except ValueError:
                            print(f"Warning: Could not parse hop number from: {hop_str}")
                            continue
                        
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
            # Ensure hop number is an integer
            hop_num = hub.get('count', 0)
            if isinstance(hop_num, str):
                try:
                    hop_num = int(hop_num)
                except ValueError:
                    hop_num = 0
            
            hop_data = {
                'hop': hop_num,
                'host': hub.get('host', 'unknown'),
                'loss_percent': float(hub.get('Loss%', 0.0)),
                'sent': int(hub.get('Snt', 0)),
                'last_ms': float(hub.get('Last', 0.0)),
                'avg_ms': float(hub.get('Avg', 0.0)),
                'best_ms': float(hub.get('Best', 0.0)),
                'worst_ms': float(hub.get('Wrst', 0.0)),
                'stddev_ms': float(hub.get('StDev', 0.0)),  # This is our jitter metric
            }
            hops.append(hop_data)
            
        return hops

    def calculate_path_health_summary(self, hops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate path health summary metrics"""
        if not hops:
            return {}
        
        # Filter out hops with 100% loss (like ??? hops that don't respond to ICMP)
        valid_hops = [hop for hop in hops if hop['loss_percent'] < 100.0]
        
        if not valid_hops:
            # If no hops respond, but we have hops, use the last hop for end-to-end metrics
            if hops:
                print("Warning: No hops respond to ICMP, using last hop for end-to-end metrics")
                end_to_end_loss = hops[-1]['loss_percent']
                end_to_end_rtt = hops[-1]['avg_ms']
                end_to_end_jitter = hops[-1]['stddev_ms']
            else:
                return {}
        else:
            # Use the last valid hop for end-to-end metrics
            last_valid_hop = valid_hops[-1]
            end_to_end_loss = last_valid_hop['loss_percent']
            end_to_end_rtt = last_valid_hop['avg_ms']
            end_to_end_jitter = last_valid_hop['stddev_ms']
        
        # For path analysis, use the actual end-to-end loss (last hop)
        # Many intermediate routers don't respond to ICMP but forward packets fine
        total_loss = hops[-1]['loss_percent'] if hops else 0.0
        
        # Average loss only for responding hops
        avg_loss = sum(hop['loss_percent'] for hop in valid_hops) / len(valid_hops) if valid_hops else 0.0
        
        # Path stability (based on jitter across responding hops)
        if valid_hops:
            avg_jitter = sum(hop['stddev_ms'] for hop in valid_hops) / len(valid_hops)
            max_jitter = max(hop['stddev_ms'] for hop in valid_hops)
            
            # Path consistency (RTT variance across responding hops)
            rtts = [hop['avg_ms'] for hop in valid_hops]
            rtt_variance = max(rtts) - min(rtts) if len(rtts) > 1 else 0.0
        else:
            avg_jitter = end_to_end_jitter
            max_jitter = end_to_end_jitter
            rtt_variance = 0.0
        
        # Health score calculation (0-100, higher is better)
        # Focus on END-TO-END performance, not intermediate hops
        loss_penalty = end_to_end_loss * 2  # 2 points per 1% end-to-end loss
        jitter_penalty = min(end_to_end_jitter * 0.5, 20)  # Max 20 points for jitter
        rtt_penalty = min(end_to_end_rtt * 0.1, 20)  # Max 20 points for high RTT
        variance_penalty = min(rtt_variance * 0.05, 10)  # Max 10 points for variance
        
        health_score = max(0, 100 - loss_penalty - jitter_penalty - rtt_penalty - variance_penalty)
        
        # Determine health status based on END-TO-END metrics
        if end_to_end_loss > 50:
            health_status = "CRITICAL"
        elif end_to_end_loss > 10:
            health_status = "POOR"
        elif health_score >= 90:
            health_status = "EXCELLENT"
        elif health_score >= 75:
            health_status = "GOOD"
        elif health_score >= 60:
            health_status = "FAIR"
        else:
            health_status = "POOR"
        
        return {
            'hop_count': len(hops),
            'valid_hops': len(valid_hops),
            'avg_loss_percent': avg_loss,     # Average of responding hops
            'end_to_end_rtt_ms': end_to_end_rtt,
            'end_to_end_jitter_ms': end_to_end_jitter,
            'end_to_end_loss_percent': end_to_end_loss,  # End-to-end loss (most important)
            'avg_jitter_ms': avg_jitter,
            'max_jitter_ms': max_jitter,
            'rtt_variance_ms': rtt_variance,
            'health_score': round(health_score, 1),
            'health_status': health_status
        }

    def clean_hostname(self, hostname: str, hop_num: int) -> str:
        """Clean hostname for Prometheus labels - remove spaces and problematic characters"""
        # Replace ??? with hop identifier
        clean_host = hostname.replace('???', f'hop_{hop_num}_silent')
        
        # Remove spaces and problematic characters
        clean_host = clean_host.replace(' ', '_').replace('"', '').replace("'", '').replace('\n', '').replace('\t', '')
        
        # Keep only alphanumeric and safe characters
        clean_host = ''.join(c for c in clean_host if c.isalnum() or c in '-_.:/')
        
        # Fallback if cleaning resulted in empty string
        if not clean_host:
            clean_host = f'hop_{hop_num}'
            
        return clean_host

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
        
        # Clean all label values - remove spaces and problematic characters
        cleaned_labels = {}
        for key, value in labels.items():
            # Convert to string and clean
            clean_value = str(value).replace(' ', '_').replace('"', '').replace("'", '').replace('\n', '').replace('\t', '')
            # Remove any other problematic characters
            clean_value = ''.join(c for c in clean_value if c.isalnum() or c in '-_.:/')
            cleaned_labels[key] = clean_value
        
        # Format as Prometheus labels
        label_parts = [f'{key}="{value}"' for key, value in cleaned_labels.items()]
        return ','.join(label_parts)

    def format_float(self, value: float, precision: int = 2) -> str:
        """Format float values with limited precision for Prometheus"""
        # Always format with specified precision, even for integers
        return f"{float(value):.{precision}f}"

    def generate_prometheus_metrics(self, hops: List[Dict[str, Any]]) -> str:
        """Generate Prometheus format metrics"""
        metrics = []
        
        # Base labels for metrics
        base_labels = self.build_labels()
        
        # Calculate path health summary
        summary = self.calculate_path_health_summary(hops)
        
        # Add metadata
        metadata_labels = f'{base_labels},port="{self.port}",protocol="{self.protocol}"'
        metrics.append(f'mtr_info{{{metadata_labels}}} 1')
        
        # Path Health Summary Metrics
        if summary:
            metrics.append(f'mtr_path_health_score{{{base_labels}}} {self.format_float(summary["health_score"], 1)}')
            metrics.append(f'mtr_path_rtt_variance_ms{{{base_labels}}} {self.format_float(summary["rtt_variance_ms"], 2)}')
            metrics.append(f'mtr_path_avg_jitter_ms{{{base_labels}}} {self.format_float(summary["avg_jitter_ms"], 2)}')
            metrics.append(f'mtr_path_max_jitter_ms{{{base_labels}}} {self.format_float(summary["max_jitter_ms"], 2)}')
            # FIXED: Use end_to_end_loss_percent instead of total_loss_percent to avoid confusion
            metrics.append(f'mtr_path_end_to_end_loss_percent{{{base_labels}}} {self.format_float(summary["end_to_end_loss_percent"], 1)}')
        
        # Separate responding and silent hops
        responding_hops = [hop for hop in hops if hop['loss_percent'] < 100.0]
        silent_hops = [hop for hop in hops if hop['loss_percent'] >= 100.0]
        
        # Only generate per-hop metrics if we have responding hops
        if responding_hops:
            # Packet loss percentage per hop (only responding hops for main metrics)
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_loss_percent{{{hop_labels}}} {self.format_float(hop["loss_percent"], 1)}')
            
            # Packets sent per hop (responding only)
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_packets_sent{{{hop_labels}}} {hop["sent"]}')
            
            # RTT metrics (responding hops only)
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_last_rtt_ms{{{hop_labels}}} {self.format_float(hop["last_ms"], 2)}')
            
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_avg_rtt_ms{{{hop_labels}}} {self.format_float(hop["avg_ms"], 2)}')
            
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_best_rtt_ms{{{hop_labels}}} {self.format_float(hop["best_ms"], 2)}')
            
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_worst_rtt_ms{{{hop_labels}}} {self.format_float(hop["worst_ms"], 2)}')
            
            # Jitter (responding hops only)
            for hop in responding_hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': 'true'
                })
                metrics.append(f'mtr_jitter_ms{{{hop_labels}}} {self.format_float(hop["stddev_ms"], 2)}')
        
        # Silent hops summary (single metric to track count)
        metrics.append(f'mtr_silent_hops_count{{{base_labels}}} {len(silent_hops)}')
        
        # Total hop count - always generate
        metrics.append(f'mtr_hop_count{{{base_labels}}} {len(hops)}')
        
        # Responding hop count - always generate
        metrics.append(f'mtr_responding_hop_count{{{base_labels}}} {len(responding_hops)}')
        
        # End-to-end metrics - always generate if we have any hops
        if hops:
            end_to_end_loss = summary.get('end_to_end_loss_percent', hops[-1]['loss_percent']) if summary else hops[-1]['loss_percent']
            end_to_end_rtt = summary.get('end_to_end_rtt_ms', hops[-1]['avg_ms']) if summary else hops[-1]['avg_ms']
            end_to_end_jitter = summary.get('end_to_end_jitter_ms', hops[-1]['stddev_ms']) if summary else hops[-1]['stddev_ms']
            
            metrics.append(f'mtr_end_to_end_loss_percent{{{base_labels}}} {self.format_float(end_to_end_loss, 1)}')
            metrics.append(f'mtr_end_to_end_avg_rtt_ms{{{base_labels}}} {self.format_float(end_to_end_rtt, 2)}')
            metrics.append(f'mtr_end_to_end_jitter_ms{{{base_labels}}} {self.format_float(end_to_end_jitter, 2)}')
        
        # All hops summary table (for debugging/reference) - always generate this if we have hops
        if hops:
            for hop in hops:
                clean_host = self.clean_hostname(hop['host'], hop['hop'])
                responding = 'true' if hop['loss_percent'] < 100.0 else 'false'
                hop_labels = self.build_labels({
                    'hop': str(hop['hop']), 
                    'host': clean_host,
                    'responding': responding
                })
                metrics.append(f'mtr_hop_info{{{hop_labels}}} 1')
        
        # REMOVED: Timestamp metric - text file collector doesn't like this
        
        return "\n".join(metrics)

    def validate_prometheus_metrics(self, metrics_content: str) -> bool:
        """Validate Prometheus metrics format"""
        lines = metrics_content.strip().split('\n')
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                print(f"Warning: Empty line found at line {i}")
                return False
            
            # Check if line matches basic prometheus format: metric_name{labels} value
            if ' ' not in line:
                print(f"Warning: Invalid format at line {i}: {line}")
                return False
            
            parts = line.rsplit(' ', 1)
            if len(parts) != 2:
                print(f"Warning: Invalid format at line {i}: {line}")
                return False
            
            metric_part, value_part = parts
            
            # Validate value is a number
            try:
                float(value_part)
            except ValueError:
                print(f"Warning: Invalid numeric value at line {i}: {value_part}")
                return False
        
        return True

    def atomic_write_metrics(self, content: str, output_file: str):
        """Atomically write metrics to file"""
        # Write to temp file first
        temp_fd, temp_path = tempfile.mkstemp(suffix='.tmp', dir=os.path.dirname(output_file))
        
        try:
            with os.fdopen(temp_fd, 'w') as f:
                f.write(content)
                # CRITICAL: Ensure file ends with newline for Prometheus parsing
                if not content.endswith('\n'):
                    f.write('\n')
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            
            # Atomic move
            os.rename(temp_path, output_file)
            # Set proper permissions for text file collector
            os.chmod(output_file, 0o644)
            print(f"? Atomically wrote metrics to {output_file}")
            
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise e

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
        
        # Remove any blank lines before writing
        clean_metrics = '\n'.join(line for line in prometheus_metrics.split('\n') if line.strip())
        
        # Validate metrics format
        if not self.validate_prometheus_metrics(clean_metrics):
            print("ERROR: Generated metrics failed validation!")
            print("First 500 chars of metrics:")
            print(clean_metrics[:500])
            sys.exit(1)
        
        print(f"? Generated {len(clean_metrics.split(chr(10)))} valid metrics")
        print("Sample metrics (first 3 lines):")
        sample_lines = clean_metrics.split('\n')[:3]
        for line in sample_lines:
            print(f"  {line}")
        
        print(f"Writing metrics to {output_file}...")
        self.atomic_write_metrics(clean_metrics, output_file)
        
        print(f"Successfully exported metrics to {output_file}")
        
        # Verify file was written correctly
        try:
            with open(output_file, 'r') as f:
                written_content = f.read()
            if len(written_content.strip()) == 0:
                print("WARNING: Written file is empty!")
            else:
                written_lines = written_content.strip().split('\n')
                print(f"? Verified: File contains {len(written_lines)} lines")
                # Check file size
                file_size = len(written_content)
                print(f"? File size: {file_size} bytes")
                # Verify ending newline
                if written_content.endswith('\n'):
                    print("? File ends with proper newline")
                else:
                    print("WARNING: File missing final newline!")
        except Exception as e:
            print(f"WARNING: Could not verify written file: {e}")
        
        # Print path health summary
        summary = self.calculate_path_health_summary(hops)
        if summary:
            print(f"\n=== PATH HEALTH SUMMARY for {self.probe_name} ===")
            print(f"Status: {summary['health_status']} (Score: {summary['health_score']}/100)")
            print(f"End-to-End Loss: {summary['end_to_end_loss_percent']:.1f}%")
            print(f"End-to-End RTT: {summary['end_to_end_rtt_ms']:.2f}ms")
            print(f"End-to-End Jitter: {summary['end_to_end_jitter_ms']:.2f}ms")
            print(f"Total Hops: {summary['hop_count']} ({summary['valid_hops']} respond to ICMP)")
            print(f"RTT Variance: {summary['rtt_variance_ms']:.2f}ms")
            print(f"Avg Path Jitter: {summary['avg_jitter_ms']:.2f}ms")
            
            if summary['valid_hops'] < summary['hop_count']:
                non_responding = summary['hop_count'] - summary['valid_hops']
                print(f"Note: {non_responding} intermediate hops don't respond to ICMP (normal behavior)")
        
        # Print detailed per-hop summary
        print(f"\n=== DETAILED HOP ANALYSIS for {self.probe_name} ===")
        for hop in hops:
            clean_host_display = self.clean_hostname(hop['host'], hop['hop'])
            if hop['loss_percent'] == 100.0:
                status = "[SILENT]"  # Silent hop (doesn't respond but forwards)
                note = " (silent - forwards but doesn't respond)"
            elif hop['loss_percent'] > 0:
                status = "[WARN] "
                note = f" ({hop['loss_percent']:.1f}% loss)"
            else:
                status = "[OK]   "
                note = ""
                
            print(f"  {status} Hop {hop['hop']:2d}: {clean_host_display:30s} "
                  f"RTT: {hop['avg_ms']:7.2f}ms "
                  f"Jitter: {hop['stddev_ms']:6.2f}ms{note}")


def load_config(config_file: str) -> Dict[str, Any]:
    """Load configuration from YAML file"""
    if yaml is None:
        print("ERROR: PyYAML is required for config mode. Install with: pip3 install PyYAML")
        sys.exit(1)
        
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing configuration file: {e}")
        sys.exit(1)


def run_config_mode(config_file: str, output_dir: str = None):
    """Run multiple probes based on configuration file"""
    config = load_config(config_file)
    
    # Get global settings
    global_config = config.get('global', {})
    if output_dir is None:
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
        protocol = probe_config.get('protocol', 'icmp')  # Default to ICMP
        labels = probe_config.get('labels', {})
        
        if not target:
            print(f"Skipping probe '{probe_name}': no target specified")
            continue
        
        print(f"\n=== Running probe: {probe_name} ===")
        print(f"Target: {target}:{port} ({protocol.upper()})")
        
        exporter = MTRPrometheusExporter(
            target=target,
            port=port,
            count=mtr_cycles,
            probe_name=probe_name,
            custom_labels=labels,
            protocol=protocol
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
                print(f"End-to-End Loss: {summary['end_to_end_loss_percent']:.1f}%")
                print(f"End-to-End RTT: {summary['end_to_end_rtt_ms']:.2f}ms")
                print(f"End-to-End Jitter: {summary['end_to_end_jitter_ms']:.2f}ms")
                print(f"Total Hops: {summary['hop_count']} ({summary['valid_hops']} respond to ICMP)")
                print(f"RTT Variance: {summary['rtt_variance_ms']:.2f}ms")
                
                if summary['valid_hops'] < summary['hop_count']:
                    non_responding = summary['hop_count'] - summary['valid_hops']
                    print(f"Note: {non_responding} intermediate hops don't respond to ICMP (normal)")
            
            # Print condensed per-hop summary
            print(f"\nDetailed hops for {probe_name}:")
            for hop in hops:
                clean_host_display = exporter.clean_hostname(hop['host'], hop['hop'])
                if hop['loss_percent'] == 100.0:
                    status = "[SILENT]"  # Silent hop
                    note = " (silent)"
                elif hop['loss_percent'] > 0:
                    status = "[WARN] "
                    note = f" ({hop['loss_percent']:.1f}% loss)"
                else:
                    status = "[OK]   "
                    note = ""
                    
                print(f"  {status} Hop {hop['hop']:2d}: {clean_host_display:30s} "
                      f"RTT: {hop['avg_ms']:7.2f}ms "
                      f"Jitter: {hop['stddev_ms']:6.2f}ms{note}")
        else:
            print(f"[ERROR] No hop data found for probe '{probe_name}'")
    
    if not all_metrics:
        print("No successful probe results")
        sys.exit(1)
    
    # FIXED: Join with single newlines and clean before writing
    combined_metrics = '\n'.join(all_metrics)
    clean_metrics = '\n'.join(line for line in combined_metrics.split('\n') if line.strip())
    
    # Validate combined metrics
    temp_exporter = MTRPrometheusExporter("temp", 443, 10, "validation", protocol="icmp")
    if not temp_exporter.validate_prometheus_metrics(clean_metrics):
        print("ERROR: Combined metrics failed validation!")
        print("First 500 chars of combined metrics:")
        print(clean_metrics[:500])
        sys.exit(1)
    
    # Write combined metrics to file atomically
    output_file = os.path.join(output_dir, "mtr_all_probes.prom")
    
    try:
        # Use the atomic write method
        write_exporter = MTRPrometheusExporter("dummy", 443, 10, "temp", protocol="icmp")  # Temporary instance for writing
        write_exporter.atomic_write_metrics(clean_metrics, output_file)
        print(f"\n=== Successfully wrote combined metrics to {output_file} ===")
        
        # Final verification
        try:
            with open(output_file, 'r') as f:
                final_content = f.read()
            final_lines = final_content.strip().split('\n')
            print(f"? Final verification: {len(final_lines)} lines written")
            if final_content.endswith('\n'):
                print("? File properly ends with newline")
            else:
                print("ERROR: File missing final newline!")
        except Exception as e:
            print(f"WARNING: Could not verify final file: {e}")
        
    except Exception as e:
        print(f"Failed to write output file: {e}")
        sys.exit(1)


def show_help():
    """Show comprehensive help message"""
    help_text = """
MTR Prometheus Exporter - Unified Script

USAGE:
  Single probe mode:
    python3 mtr_exporter.py TARGET [OPTIONS]
    
  Config mode:
    python3 mtr_exporter.py --config CONFIG_FILE [OPTIONS]

SINGLE PROBE MODE:
  TARGET                    Target hostname or IP address
  -p, --port PORT          Target port (default: 443)
  -c, --count COUNT        Number of pings per hop (default: 10)
  -i, --interval INTERVAL  Interval between pings in seconds (default: 1)
  -o, --output FILE        Output file (default: mtr_metrics.prom)
  --probe-name NAME        Probe name for metrics (default: default)
  --protocol PROTOCOL      Protocol: icmp, tcp, udp (default: icmp)
  --label KEY=VALUE        Add custom label (can be used multiple times)

CONFIG MODE:
  --config FILE            Configuration file path
  --output-dir DIR         Override output directory from config

EXAMPLES:
  # Single probe with ICMP (default)
  python3 mtr_exporter.py www.google.com
  
  # DNS testing with UDP on port 53
  python3 mtr_exporter.py 8.8.8.8 --port 53 --protocol udp --label service=dns
  
  # HTTPS testing with TCP on port 443
  python3 mtr_exporter.py google.com --port 443 --protocol tcp --label service=https
  
  # Config mode
  python3 mtr_exporter.py --config mtr_config.yaml
  python3 mtr_exporter.py --config mtr_config.yaml --output-dir /usr/share/node_exporter/textfile_collector

SAMPLE CONFIG FILE (mtr_config.yaml):
  global:
    output_dir: /usr/share/node_exporter/textfile_collector
    mtr_cycles: 10
    
  probes:
    - name: cloudflare_dns
      target: 1.1.1.1
      port: 53
      protocol: udp
      labels:
        service: dns
        
    - name: google_https
      target: www.google.com
      port: 443
      protocol: tcp
      labels:
        service: https
        
    - name: ping_test
      target: 8.8.8.8
      protocol: icmp
      labels:
        service: ping

PROTOCOLS:
  icmp - Traditional ping/traceroute (ignores port)
  tcp  - TCP traceroute to specific port
  udp  - UDP traceroute to specific port
  
NOTES:
  - For DNS testing, use protocol: udp with port: 53
  - For HTTPS testing, use protocol: tcp with port: 443
  - ICMP doesn't use port numbers
"""
    print(help_text)


def main():
    # Check for help first
    if '--help' in sys.argv or '-h' in sys.argv:
        show_help()
        return
    
    # Check if --config is in args, if so use config mode
    if '--config' in sys.argv:
        if yaml is None:
            print("ERROR: Config mode requires PyYAML. Install with: pip3 install PyYAML")
            sys.exit(1)
            
        parser = argparse.ArgumentParser(description='MTR to Prometheus Exporter - Config Mode')
        parser.add_argument('--config', required=True, help='Configuration file path')
        parser.add_argument('--output-dir', help='Override output directory from config')
        args = parser.parse_args()
        
        run_config_mode(args.config, args.output_dir)
        return
    
    # Otherwise use single probe mode (backwards compatible)
    parser = argparse.ArgumentParser(description='Export MTR metrics to Prometheus format')
    parser.add_argument('target', help='Target hostname or IP address')
    parser.add_argument('-p', '--port', type=int, default=443, help='Target port (default: 443)')
    parser.add_argument('-c', '--count', type=int, default=10, help='Number of pings per hop (default: 10)')
    parser.add_argument('-i', '--interval', type=int, default=1, help='Interval between pings in seconds (default: 1)')
    parser.add_argument('-o', '--output', default='mtr_metrics.prom', help='Output file (default: mtr_metrics.prom)')
    parser.add_argument('--probe-name', default='default', help='Probe name for metrics (default: default)')
    parser.add_argument('--protocol', choices=['icmp', 'tcp', 'udp'], default='icmp', help='Protocol to use (default: icmp)')
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
        custom_labels=custom_labels,
        protocol=args.protocol
    )
    
    exporter.export_to_file(args.output)


if __name__ == '__main__':
    main()

