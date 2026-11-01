@echo off
cd /d "%~dp0"
title Stop Amul Failover Supervisor
echo ====================================================================
echo             STOPPING AMUL FAILOVER SUPERVISOR ^& BOT
echo ====================================================================
echo.
echo Terminating running Python supervisor and server processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*failover_manager.py*' -or $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo [OK] All Amul supervisor and server processes stopped.
echo.
pause
