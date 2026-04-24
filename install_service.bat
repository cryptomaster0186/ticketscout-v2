@echo off
REM ============================================================
REM  install_service.bat
REM  Installs TicketScout as a Windows Service using NSSM.
REM  Run this as Administrator.
REM  Download NSSM from: https://nssm.cc/download
REM ============================================================

echo Checking for Administrator rights...
net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Run this script as Administrator (right-click -> Run as administrator)
    pause
    exit /b 1
)

REM ── Config ───────────────────────────────────────────────────
set SERVICE_NAME=TicketScout
set APP_DIR=%~dp0
set PYTHON_EXE=python
set NSSM=nssm.exe

REM Try to find NSSM in same folder first, then PATH
if exist "%APP_DIR%nssm.exe" set NSSM=%APP_DIR%nssm.exe

where %NSSM% >nul 2>&1
if errorlevel 1 (
    echo ERROR: nssm.exe not found.
    echo Download from https://nssm.cc/download and place nssm.exe in:
    echo   %APP_DIR%
    pause
    exit /b 1
)

REM ── Find Python path ─────────────────────────────────────────
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i
echo Using Python: %PYTHON_PATH%

REM ── Remove old service if exists ─────────────────────────────
%NSSM% status %SERVICE_NAME% >nul 2>&1
if not errorlevel 1 (
    echo Removing existing service...
    %NSSM% stop %SERVICE_NAME% >nul 2>&1
    %NSSM% remove %SERVICE_NAME% confirm >nul 2>&1
)

REM ── Install service ──────────────────────────────────────────
echo Installing %SERVICE_NAME% service...

%NSSM% install %SERVICE_NAME% "%PYTHON_PATH%"
%NSSM% set %SERVICE_NAME% AppParameters "run_production.py --host 0.0.0.0 --port 5001 --threads 4"
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "TicketScout Dashboard"
%NSSM% set %SERVICE_NAME% Description "Entradas TicketScout web dashboard"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppStdout "%APP_DIR%ticketscout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%APP_DIR%ticketscout-error.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 5242880
%NSSM% set %SERVICE_NAME% AppRestartDelay 5000

REM ── Start service ─────────────────────────────────────────────
echo Starting service...
%NSSM% start %SERVICE_NAME%

echo.
echo ============================================================
echo   Service installed and started!
echo   Access: http://YOUR-SERVER-IP:5001
echo.
echo   Manage with:
echo     nssm start %SERVICE_NAME%
echo     nssm stop %SERVICE_NAME%
echo     nssm restart %SERVICE_NAME%
echo     nssm remove %SERVICE_NAME% confirm
echo ============================================================
pause
