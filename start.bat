@echo off
title TicketScout Production Server
cd /d "%~dp0"

echo ============================================================
echo   TicketScout - Starting Production Server
echo ============================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM Install dependencies if needed
echo Checking dependencies...
pip install -r requirements.txt --quiet

echo.
echo Starting server on http://0.0.0.0:5001
echo Press Ctrl+C to stop.
echo.

python run_production.py --host 0.0.0.0 --port 5001 --threads 4

pause
