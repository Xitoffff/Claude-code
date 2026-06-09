@echo off
REM Fiverr Radar - one-click launcher for Windows
cd /d "%~dp0"
echo Installing dependencies...
python -m pip install -r requirements.txt
echo Starting Fiverr Radar...
start "" http://127.0.0.1:8000
python run.py
pause
