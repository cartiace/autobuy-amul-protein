@echo off
cd /d "%~dp0"
title Remove Amul Auto-Start on Windows Boot

echo ====================================================================
echo         AMUL FAILOVER SUPERVISOR - UNINSTALL AUTO-START
echo ====================================================================
echo.

set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AmulFailoverBackup.lnk"

if exist "%SHORTCUT%" (
    del /f /q "%SHORTCUT%"
    echo [SUCCESS] Removed Amul Failover Supervisor from Windows Startup.
    echo The bot will no longer start automatically when Windows boots.
) else (
    echo [INFO] Startup shortcut not found (already uninstalled).
)

echo.
pause
