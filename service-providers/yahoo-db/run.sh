#!/bin/bash

# Stock Data Service - Flask Web Application Run Script
# This script starts the Flask web server for the stock data service

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
VENV_DIR="$PROJECT_ROOT/yahoodb"
VENV_PYTHON="$VENV_DIR/bin/python"

# Check if virtual environment exists, create if not
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Virtual environment not found at $VENV_DIR"
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
    echo "Virtual environment created successfully."

    echo "Installing dependencies from requirements.txt..."
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to install dependencies."
        exit 1
    fi
    echo "Dependencies installed successfully."
fi

# Change to script directory
cd "$SCRIPT_DIR"

# Run the Flask application
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║           Stock Data Service v0.0.0.7                     ║"
echo "║   Flask Web Application for Yahoo Finance Data            ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Starting Flask Web Server..."
echo "Using Python: $VENV_PYTHON"
echo ""
echo "Dashboard will be available at: http://localhost:5000"
echo ""

"$VENV_PYTHON" main.py
