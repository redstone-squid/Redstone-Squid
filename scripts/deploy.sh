#!/bin/bash
#
# deploy.sh - Script to be run in the root of the project directory to deploy Redstone Squid.
# It pulls the latest changes from git, syncs pip requirements, and starts the app.
#
set -euo pipefail

# path to a file that stores the PID of the running app
PIDFILE=".app.pid"

# Function to show usage
usage() {
    echo "Usage: $0 [-d]"
    echo "  -d    Run in detached mode"
    exit 1
}

# Initialize detach flag
detach=false

# Parse command line arguments
while getopts "d" opt; do
    case $opt in
        d)
            detach=true
            ;;
        *)
            usage
            ;;
    esac
done

# Pull latest changes from git
echo "Pulling latest changes..."
git pull

# Install newest dependencies
echo "Syncing dependencies"
# Alternatively: pip-sync requirements/base.txt requirements/dev.txt
uv sync

# Kill existing app.py process
echo "Killing existing app.py process..."
if [ -f "$PIDFILE" ]; then
  echo "Found PID file in $PIDFILE"
  PID=$(<"$PIDFILE")
  if kill -0 $PID 2>/dev/null; then
    echo "Stopping app (PID $PID)…"
    kill $PID
    rm "$PIDFILE"
  else
    echo "No process $PID; removing stale PID file."
    rm "$PIDFILE"
  fi

  # Double-check if the process is still running
  if kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID is still running after kill command. Exiting."
    exit 1
  fi
else
  echo "No PID file; Assuming no app is running."
  echo "If you have manually started the app, please stop it before running this script."
fi

# Run the application
if [ "$detach" = true ]; then
    echo "Starting app in detached mode..."
    nohup uv run app.py > app.log 2>&1 &
    echo "$!" > "$PIDFILE"
    echo "App started in background. Check app.log for output."
else
    echo "Starting app in foreground..."
    uv run app.py &
    echo "$!" > "$PIDFILE"
    wait
fi
