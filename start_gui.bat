@echo off
REM Fiverr Radar - native desktop GUI launcher for Windows
cd /d "%~dp0"
echo Installing dependencies (first run may take a minute)...
python -m pip install -r requirements.txt
echo Starting Fiverr Radar desktop app...
python run_gui.py
pause
