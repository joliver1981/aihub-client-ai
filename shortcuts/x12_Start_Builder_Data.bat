@echo off
setlocal enabledelayedexpansion
echo AI Hub Services Manager (AI-DEV Builder Data) - Start/Restart Script
echo ========================================================

:: Set the path to the Anaconda/Miniconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"
:: Set the project folder path
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

echo.
echo Checking for running services...
echo.

:: Define the scripts we're looking for
set SCRIPTS=builder_data\main.py

:: Kill each script's process
for %%s in (%SCRIPTS%) do (
    echo Looking for processes running %%s...
    
    :: Get process IDs directly from WMIC for processes containing the script name
    for /f "skip=1 tokens=2 delims=," %%p in ('wmic process where "name='python.exe' and commandline like '%%%%s%%'" get processid /format:csv 2^>nul') do (
        if not "%%p"=="" (
            echo [!] Found process %%p running %%s - killing it...
            taskkill /PID %%p /F
            if !errorlevel! equ 0 (
                echo [X] Successfully killed process %%p
            ) else (
                echo [E] Failed to kill process %%p
            )
        )
    )
)

:: Alternative method: Kill all processes at once using a single WMIC command
echo.
echo Using alternative method to ensure all processes are stopped...
wmic process where "name='python.exe' and (commandline like '%%builder_data%%main.py%%')" delete >nul 2>&1

:: Also kill by window title
echo Cleaning up any remaining windows...
taskkill /FI "WINDOWTITLE eq AIHub-DEV Builder Data*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Administrator:*AIHub-DEV Builder Data*" /F >nul 2>&1

:: Wait for processes to terminate
echo.
echo Waiting for processes to terminate...
timeout /t 3 /nobreak >nul

echo.
echo Starting all AI Hub (AI-DEV) services...
echo.

:: Start services

timeout /t 3 /nobreak >nul

echo [12/12] Starting Builder Data Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Builder Data" /D "%PROJECT_PATH%\builder_data" cmd /k "color 05 && title AIHub-DEV Builder Data && "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"


echo.
echo ============================================
echo All AI-DEV services have been launched!
echo ============================================
echo.
echo Each service is running in its own window:
echo - Green  : Main Application (aihub2.1)
echo - Cyan   : Document API Server (aihubant)
echo - Red    : Document Job Queue (aihubant)
echo - Purple : JSS Main Application (jss)
echo - Yellow : Vector API Server (aihubvector)
echo - White  : Agent API Server (aihub2.1)
echo - Aqua   : MCP Gateway (aihubmcp) - port 5071
echo - Blue   : Cloud Storage Gateway (aihubcloudgateway) - port 5081
echo - Brown  : Builder Service (aihubbuilder) - port 8100
echo - Magenta: Builder Data (aihubbuilder) - port 8200
echo.
echo Source: %PROJECT_PATH%
echo.
echo To stop a service, close its window or press Ctrl+C in it.
echo To restart all services, run this script again.
echo.
REM pause
endlocal
