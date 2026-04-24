@echo off
REM ============================================================
REM  setup_backup_task.bat
REM  Creates a Windows Task Scheduler job that runs the DB backup
REM  every day at 03:00.
REM  Run as Administrator.
REM ============================================================

net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Run as Administrator.
    pause
    exit /b 1
)

set TASK_NAME=TicketScoutBackup
set APP_DIR=%~dp0
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i

echo Creating scheduled task: %TASK_NAME%
echo Python: %PYTHON_PATH%
echo Script: %APP_DIR%backup_db.py
echo Schedule: Daily at 03:00
echo.

REM Remove existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create new task — runs daily at 3am, starts on boot if missed
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_PATH%\" \"%APP_DIR%backup_db.py\"" ^
  /sc DAILY ^
  /st 03:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f ^
  /np

if errorlevel 1 (
    echo ERROR: Failed to create task.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Backup task created!
echo   Runs daily at 03:00 using the SYSTEM account.
echo.
echo   Test it now:
echo     schtasks /run /tn %TASK_NAME%
echo.
echo   View logs:
echo     %APP_DIR%backup.log
echo ============================================================
pause
