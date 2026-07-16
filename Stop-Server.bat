@echo off
title Stop STAR/SID Designator
echo ===================================================
echo   ✈  Stopping STAR/SID Designator Bridge Server... 
echo ===================================================
echo.

taskkill /f /im python.exe /fi "WINDOWTITLE eq STAR/SID Designator Server" >nul 2>&1

if %errorlevel% equ 0 (
    echo [SUCCESS] Server process has been stopped successfully.
) else (
    echo [INFO] No active STAR/SID Designator Server process was found.
)

echo.
timeout /t 3
