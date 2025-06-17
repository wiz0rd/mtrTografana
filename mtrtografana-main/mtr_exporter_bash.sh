#!/bin/bash
#
# MTR Prometheus Exporter Wrapper Script
# This script safely updates MTR metrics for Prometheus text file collector
#

# Configuration
SCRIPT_DIR="/netsec/python/projects/mtrtografana"  # Your actual path
CONFIG_FILE="$SCRIPT_DIR/mtr_config.yaml"
OUTPUT_FILE="/usr/share/node_exporter/textfile_collector/mtr_all_probes.prom"
PYTHON_SCRIPT="$SCRIPT_DIR/mtr_exporter.py"
LOG_FILE="$SCRIPT_DIR/mtr_exporter.log"  # Changed to use script directory

# Function to log messages with timestamp
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Function to log errors
log_error() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: $1" | tee -a "$LOG_FILE" >&2
}

# Start execution
log_message "Starting MTR metrics collection"

# Check if required files exist
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    log_error "Python script not found: $PYTHON_SCRIPT"
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error "Config file not found: $CONFIG_FILE"
    exit 1
fi

# Check if textfile collector directory exists
TEXTFILE_DIR=$(dirname "$OUTPUT_FILE")
if [[ ! -d "$TEXTFILE_DIR" ]]; then
    log_error "Textfile collector directory not found: $TEXTFILE_DIR"
    exit 1
fi

# Check if we can write to the textfile directory
if [[ ! -w "$TEXTFILE_DIR" ]]; then
    log_error "Cannot write to textfile directory: $TEXTFILE_DIR"
    exit 1
fi

# Remove old metrics file if it exists
if [[ -f "$OUTPUT_FILE" ]]; then
    log_message "Removing old metrics file: $OUTPUT_FILE"
    rm -f "$OUTPUT_FILE"
    if [[ $? -ne 0 ]]; then
        log_error "Failed to remove old metrics file"
        exit 1
    fi
else
    log_message "No existing metrics file to remove"
fi

# Change to script directory and run the MTR exporter
log_message "Running MTR exporter with config: $CONFIG_FILE"
cd "$SCRIPT_DIR"

# Run the Python script and capture output
if python3 mtr_exporter.py --config mtr_config.yaml >> "$LOG_FILE" 2>&1; then
    # Check if output file was created
    if [[ -f "$OUTPUT_FILE" ]]; then
        # Get file size and line count
        FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null)
        LINE_COUNT=$(wc -l < "$OUTPUT_FILE")
        log_message "SUCCESS: Generated $OUTPUT_FILE ($LINE_COUNT lines, $FILE_SIZE bytes)"
        
        # Verify file ends with newline
        if [[ $(tail -c1 "$OUTPUT_FILE" | wc -l) -eq 1 ]]; then
            log_message "File properly ends with newline"
        else
            log_message "WARNING: File missing final newline"
        fi
        
        # Show first few metrics for verification
        log_message "Sample metrics:"
        head -3 "$OUTPUT_FILE" | while read line; do
            log_message "  $line"
        done
        
    else
        log_error "MTR exporter completed but output file was not created: $OUTPUT_FILE"
        exit 1
    fi
else
    log_error "MTR exporter failed"
    exit 1
fi

log_message "MTR metrics collection completed successfully"
exit 0
