#!/bin/bash
# Flow-Local Toggle Script for Ubuntu
# Binds to an OS shortcut to send SIGUSR1 to the flow.py daemon.

PID_FILE="/tmp/flow_local.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    # Verify the process is actually running
    if kill -0 "$PID" 2>/dev/null; then
        kill -SIGUSR1 "$PID"
        exit 0
    fi
fi

# If we get here, the daemon isn't running
notify-send -u critical "Flow-Local" "Daemon is not running! Start the systemd service."