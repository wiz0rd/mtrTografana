#!/bin/bash
# MTR Prometheus Exporter Automation Script
# This script can be run via cron or systemd timer for continuous monitoring

# Configuration
TARGET="www.google.com"
PORT=80
OUTPUT_DIR="./output"
OUTPUT_FILE="$OUTPUT_DIR/mtr_google.prom"
TEMP_FILE="$OUTPUT_DIR/mtr_google.prom.tmp"
LOG_FILE="./mtr_exporter.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/mtr_exporter.py"
CUSTOM_LABEL='environment="test",service="google_com"'
LOG_LEVEL="INFO"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check if output directory exists
if [ ! -d "$OUTPUT_DIR" ]; then
    log "Creating output directory: $OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"
fi

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

log "Starting MTR export for $TARGET:$PORT"

# Run the Python script and write to temporary file first
if python3 "$PYTHON_SCRIPT" "$TARGET" \
    --port "$PORT" \
    --output "$TEMP_FILE" \
    --custom-label "$CUSTOM_LABEL" \
    --log-level "$LOG_LEVEL" \
    --log-file "$LOG_FILE"; then
    # Atomically move the temp file to final location
    mv "$TEMP_FILE" "$OUTPUT_FILE"
    log "Successfully updated metrics file: $OUTPUT_FILE"
    
    # Set proper permissions if needed
    chmod 644 "$OUTPUT_FILE"
    
    # Optional: Print summary
    if [ "$1" = "--verbose" ]; then
        log "Metrics summary:"
        grep "mtr_end_to_end" "$OUTPUT_FILE" | head -3 | while read line; do
            log "  $line"
        done
    fi
else
    log "ERROR: MTR export failed"
    # Clean up temp file if it exists
    [ -f "$TEMP_FILE" ] && rm -f "$TEMP_FILE"
    exit 1
fi

log "MTR export completed successfully"
