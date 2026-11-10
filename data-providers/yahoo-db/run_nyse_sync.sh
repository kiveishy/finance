#!/bin/bash

# NYSE Ticker Sync Script
# This script runs the NYSE ticker synchronization

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
VENV_PYTHON="$PROJECT_ROOT/yahoodb/bin/python"

# Check if virtual environment exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found at $VENV_PYTHON"
    echo "Please create the virtual environment first."
    exit 1
fi

# Change to script directory
cd "$SCRIPT_DIR"

# Run the NYSE sync script with any passed arguments
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║           NYSE Ticker Sync Script v0.0.0.6                ║"
echo "║   Download and sync NYSE tickers to local database        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Using Python: $VENV_PYTHON"
echo ""

"$VENV_PYTHON" sync_nyse.py "$@"
