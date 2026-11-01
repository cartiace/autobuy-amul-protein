@echo off
title Amul Failover Supervisor (Backup Node)
echo ====================================================================
echo      AMUL HIGH-AVAILABILITY FAILOVER SUPERVISOR (WINDOWS PC)        
echo ====================================================================

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH! Please install Python 3.10+.
    pause
    exit /b 1
)

:: Activate virtual environment if present, otherwise use system python
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else if exist ..\venv\Scripts\activate.bat (
    call ..\venv\Scripts\activate.bat
)

:: Set default role for Windows backup node if not set
if "%NODE_ROLE%"=="" set NODE_ROLE=backup
if "%NODE_ID%"=="" set NODE_ID=windows-pc

:start
:: Ensure no orphaned bot instance is lingering in the background
powershell -NoProfile -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Starting Failover Manager as %NODE_ROLE% node (%NODE_ID%)...
python failover_manager.py

if %errorlevel% neq 0 (
    echo [WARNING] Failover Manager exited with code %errorlevel%. Restarting in 10s...
    timeout /t 10
    goto :start
)
pause
