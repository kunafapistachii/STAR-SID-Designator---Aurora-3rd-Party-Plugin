@echo off
title STAR/SID Designator Server
echo ===================================================
echo   ✈  STAR/SID Designator Bridge Server Launcher  
echo ===================================================
echo.

:: Ensure working directory is the script directory
cd /d "%~dp0"

:: Set python path to project root
set PYTHONPATH=.

:: Check if virtual environment exists
if exist ".venv\Scripts\python.exe" (
    echo [INFO] Found local virtual environment (.venv).
    echo [INFO] Starting server...
    echo.
    ".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, '.'); from backend.server import main; import asyncio; asyncio.run(main())"
) else (
    echo [WARNING] Local virtual environment (.venv) not found.
    echo [INFO] Trying global system Python...
    echo [INFO] Starting server...
    echo.
    python -c "import sys; sys.path.insert(0, '.'); from backend.server import main; import asyncio; asyncio.run(main())"
)

echo.
echo [INFO] Server stopped.
pause
