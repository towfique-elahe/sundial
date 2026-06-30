@echo off
REM Run Sundial directly with Python (no build step).
python -m pip install -r requirements.txt >nul 2>&1
start "" pythonw sundial.py
