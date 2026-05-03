@echo off
REM Start (or restart) the model_tester debug app.
REM Usage: start_model_tester.bat [port]   (default port 6099)

setlocal

set "MODEL_TESTER_PORT=%~1"
if "%MODEL_TESTER_PORT%"=="" set "MODEL_TESTER_PORT=6099"

REM Kill any python.exe currently bound to the target port (best-effort restart)
echo Stopping any existing model_tester on port %MODEL_TESTER_PORT% ...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%MODEL_TESTER_PORT% " ^| findstr "LISTENING"') do (
    echo Killing PID %%P
    taskkill /F /PID %%P >nul 2>&1
)

REM Activate the conda env that has Flask + openai + requests installed
set "CONDA_PATH=C:\Users\james\miniconda3"
call "%CONDA_PATH%\Scripts\activate.bat"
call conda activate aihub2.1
if errorlevel 1 (
    echo Failed to activate aihub2.1 conda env. Aborting.
    exit /b 1
)

cd /d "%~dp0"

echo.
echo Launching model_tester on http://localhost:%MODEL_TESTER_PORT%
echo Press Ctrl-C to stop.
echo.

REM Open browser ~3 seconds after launch (gives Flask time to bind)
start "" /B cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:%MODEL_TESTER_PORT%"

set "MODEL_TESTER_PORT=%MODEL_TESTER_PORT%"
python app.py

endlocal
