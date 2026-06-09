#!/usr/bin/env bash
# Fiverr Radar - one-click launcher for macOS / Linux
cd "$(dirname "$0")"
echo "Installing dependencies..."
python3 -m pip install -r requirements.txt
echo "Starting Fiverr Radar on http://127.0.0.1:8000 ..."
python3 run.py
