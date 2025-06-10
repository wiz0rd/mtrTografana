#!/bin/bash
# MTR Prometheus Exporter Automation Script - Multi-probe Support
# This script can run single probes or multiple probes from a config file
# Usage: ./mtr_metrics_multi.py [OPTIONS] [TARGET]
#   Single probe: ./mtr_metrics_multi.py www.google.com --port 80
#   Config mode:  ./mtr_metrics_multi.py --config mtr_config.yaml

# Default Configuration (used when no config file is provided)
DEFAULT_TARGET="www.google.com"
DEFAULT_PORT=80
DEFAULT_OUTPUT_DIR="./output"
DEFAULT_LOG_FILE="./mtr_exporter.log"
DEFAULT_LOG_LEVEL="INFO"
DEFAULT_CUSTOM_LABEL='environment="test",service="default"'

# Script paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/mtr_exporter.py"
DEFAULT_CONFIG="$SCRIPT_DIR/mtr_config.yaml"

# Parse command line arguments
CONFIG_FILE=""
TARGET=""
PORT=""
OUTPUT_DIR=""
LOG_FILE=""
LOG_LEVEL=""
CUSTOM_LABEL=""
VERBOSE=false

show_help() {
    cat << EOF
MTR Prometheus Exporter - Multi-probe Support

Usage:
  $0 [OPTIONS] [TARGET]                 # Single probe mode
  $0 --config CONFIG_FILE [OPTIONS]    # Multi-probe config mode

Single Probe Mode:
  TARGET                    Target hostname or IP address
  --port PORT              Target port number
  --output-dir DIR         Output directory (default: ./output)
  --log-file FILE          Log file path (default: ./mtr_exporter.log)
  --log-level LEVEL        Log level (default: INFO)
  --custom-label LABELS    Custom Prometheus labels

Config Mode:
  --config FILE            Configuration file path

General Options:
  --verbose               Show verbose output
  --help                  Show this help message

Examples:
  $0 www.google.com --port 80
  $0 --config mtr_config.yaml
  $0 8.8.8.8 --port 53 --custom-label 'service="dns"'
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --log-file)
            LOG_FILE="$2"
            shift 2
            ;;
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --custom-label)
            CUSTOM_LABEL="$2"
            shift 2
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
        *)
            if [ -z "$TARGET" ]; then
                TARGET="$1"
            else
                echo "Unexpected argument: $1"
                show_help
                exit 1
            fi
            shift
            ;;
    esac
done

# Set defaults
OUTPUT_DIR="${OUTPUT_DIR:-$DEFAULT_OUTPUT_DIR}"
LOG_FILE="${LOG_FILE:-$DEFAULT_LOG_FILE}"
LOG_LEVEL="${LOG_LEVEL:-$DEFAULT_LOG_LEVEL}"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check if Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    log "ERROR: Python script not found at $PYTHON_SCRIPT"
    exit 1
fi

# Check if mtr is installed
if ! command -v mtr &> /dev/null; then
    log "ERROR: mtr command not found. Please install mtr package."
    exit 1
fi

# Check if PyYAML is available (needed for config mode)
if ! python3 -c "import yaml" &> /dev/null; then
    log "WARNING: PyYAML not found. Config mode will not work. Install with: pip3 install PyYAML"
    if [ -n "$CONFIG_FILE" ]; then
        log "ERROR: Config mode requested but PyYAML not available"
        exit 1
    fi
fi

# Create output directory
if [ ! -d "$OUTPUT_DIR" ]; then
    log "Creating output directory: $OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"
fi

# Determine mode and execute
if [ -n "$CONFIG_FILE" ]; then
    # Config mode
    log "Running in config mode with file: $CONFIG_FILE"
    
    if [ ! -f "$CONFIG_FILE" ]; then
        log "ERROR: Configuration file not found: $CONFIG_FILE"
        exit 1
    fi
    
    if python3 "$PYTHON_SCRIPT" --config "$CONFIG_FILE"; then
        log "Multi-probe MTR export completed successfully"
        
        # Show summary if verbose
        if [ "$VERBOSE" = true ]; then
            METRICS_FILE="$OUTPUT_DIR/mtr_all_probes.prom"
            if [ -f "$METRICS_FILE" ]; then
                log "Metrics summary:"
                grep "mtr_end_to_end_avg_rtt_ms" "$METRICS_FILE" | head -5 | while read line; do
                    log "  $line"
                done
            fi
        fi
    else
        log "ERROR: Multi-probe MTR export failed"
        exit 1
    fi
    
else
    # Single probe mode
    TARGET="${TARGET:-$DEFAULT_TARGET}"
    PORT="${PORT:-$DEFAULT_PORT}"
    CUSTOM_LABEL="${CUSTOM_LABEL:-$DEFAULT_CUSTOM_LABEL}"
    
    OUTPUT_FILE="$OUTPUT_DIR/mtr_$(echo "$TARGET" | tr '.' '_').prom"
    TEMP_FILE="$OUTPUT_FILE.tmp"
    
    log "Running in single probe mode for $TARGET:$PORT"
    
    if python3 "$PYTHON_SCRIPT" "$TARGET" \
        --port "$PORT" \
        --output "$TEMP_FILE" \
        --custom-label "$CUSTOM_LABEL" \
        --log-level "$LOG_LEVEL" \
        --log-file "$LOG_FILE"; then
        
        # Atomically move the temp file to final location
        mv "$TEMP_FILE" "$OUTPUT_FILE"
        log "Successfully updated metrics file: $OUTPUT_FILE"
        
        # Set proper permissions
        chmod 644 "$OUTPUT_FILE"
        
        # Show summary if verbose
        if [ "$VERBOSE" = true ]; then
            log "Metrics summary:"
            grep "mtr_end_to_end" "$OUTPUT_FILE" | head -3 | while read line; do
                log "  $line"
            done
        fi
    else
        log "ERROR: Single probe MTR export failed"
        # Clean up temp file if it exists
        [ -f "$TEMP_FILE" ] && rm -f "$TEMP_FILE"
        exit 1
    fi
fi

log "MTR export completed successfully"