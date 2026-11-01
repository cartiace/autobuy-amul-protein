@echo off
cd /d "%~dp0"
title Install Amul Auto-Start on Windows Boot

echo ====================================================================
echo         AMUL FAILOVER SUPERVISOR - WINDOWS AUTO-START INSTALLER
echo ====================================================================
echo.
echo This will configure the backup failover bot to start automatically
echo whenever your Windows PC turns on or you log in.
echo.
echo Choose startup mode:
echo   [1] Silent Background (No command window popups - Recommended)
echo   [2] Visible Console Window (Shows live terminal on startup)
echo.
set /p choice="Enter choice [1 or 2] (Default is 1): "
if "%choice%"=="" set choice=1

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_FOLDER%\AmulFailoverBackup.lnk"

if "%choice%"=="2" (
    echo.
    echo Configuring Visible Console Window shortcut...
    powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%~dp0start_failover.bat'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
) else (
    echo.
    echo Configuring Silent Background shortcut...
    powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%~dp0run_background.vbs\"'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
)

echo.
if exist "%SHORTCUT%" (
    echo ====================================================================
    echo [SUCCESS] Amul Failover Supervisor is now installed to Windows Startup!
    echo ====================================================================
    echo Startup Shortcut : "%SHORTCUT%"
    echo Working Folder    : "%~dp0"
    echo.
    echo Next time your Windows PC boots, it will automatically stand by
    echo as the backup node for your laptop.
    echo.
    echo Helpful management tools:
    echo   - To start right now       : double-click 'start_failover.bat' or 'run_background.vbs'
    echo   - To view activity anytime : open 'failover_manager.log'
    echo   - To stop the service      : double-click 'stop_failover.bat'
    echo   - To remove auto-start     : double-click 'uninstall_autostart.bat'
    echo ====================================================================
) else (
    echo [ERROR] Could not create shortcut in Windows Startup folder.
)

echo.
pause
