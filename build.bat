@echo off
REM Build Sundial into a single windowless .exe (no console).
REM Run this on Windows with Python 3.9+ installed.

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

echo Building Sundial.exe...
python -m PyInstaller --noconfirm --onefile --windowed --name Sundial ^
  --collect-data tzdata sundial.py

echo.
echo Done. Your app is at: dist\Sundial.exe
echo Double-click it, then use the tray icon ^> Settings to configure.
pause
