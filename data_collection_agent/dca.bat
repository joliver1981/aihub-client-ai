@echo off
REM ============================================================
REM  dca.bat — control script for the standalone Data Collection
REM            Agent app while testing.
REM
REM  Usage:
REM     dca start     - start the server in the background
REM     dca stop      - kill the server (anything listening on the port)
REM     dca restart   - stop, then start
REM     dca open      - open the wizard in your default browser
REM     dca status    - show whether the server is up
REM     dca logs      - tail the log file (Ctrl+C to exit)
REM
REM  Environment knobs (override before running):
REM     set DCA_PORT=5099
REM     set DCA_HOST=0.0.0.0    (default — user-facing, bind all interfaces)
REM     set DCA_HOST=127.0.0.1   (loopback only — local dev)
REM     set DCA_PYTHON=C:\Users\james\miniconda3\envs\aihub-dca-voice\python.exe
REM ============================================================

setlocal ENABLEEXTENSIONS

REM ---- Defaults -------------------------------------------------
if "%DCA_PORT%"=="" set DCA_PORT=5099
if "%DCA_PYTHON%"=="" set DCA_PYTHON=C:\Users\james\miniconda3\envs\aihub-dca-voice\python.exe

REM Test mode: bypasses identity / ownership / admin-role checks so this
REM script can drive the app without logging in or minting JWTs.
REM Set DATA_COLLECTION_TEST_MODE=False (or remove this line) to exercise
REM the production-strict flow (admin role required for builder/admin,
REM session ownership enforced across users).
if "%DATA_COLLECTION_TEST_MODE%"=="" set DATA_COLLECTION_TEST_MODE=True

REM This script lives in <project>\shortcuts\ ; the runner and logs live one
REM level up at the project root. Allow override via DCA_PROJECT_DIR.
set DCA_BAT_DIR=%~dp0
if "%DCA_BAT_DIR:~-1%"=="\" set DCA_BAT_DIR=%DCA_BAT_DIR:~0,-1%
if "%DCA_PROJECT_DIR%"=="" (
    for %%I in ("%DCA_BAT_DIR%\..") do set DCA_PROJECT_DIR=%%~fI
)
set DCA_DIR=%DCA_PROJECT_DIR%
set DCA_LOG=%DCA_DIR%\logs\dca_standalone.log
set DCA_RUNNER=%DCA_DIR%\run_dca.py
set DCA_URL_GALLERY=http://127.0.0.1:%DCA_PORT%/data-collection/
set DCA_URL_BUILDER=http://127.0.0.1:%DCA_PORT%/data-collection/builder
set DCA_URL_EXAMPLE=http://127.0.0.1:%DCA_PORT%/data-collection/example
set DCA_URL_HEALTH=http://127.0.0.1:%DCA_PORT%/healthz

REM ---- Sanity checks --------------------------------------------
if not exist "%DCA_PYTHON%" (
    echo [dca] Python executable not found: %DCA_PYTHON%
    echo [dca] Override with: set DCA_PYTHON=^<full path^>
    exit /b 1
)
if not exist "%DCA_RUNNER%" (
    echo [dca] run_dca.py not found at: %DCA_RUNNER%
    exit /b 1
)
if not exist "%DCA_DIR%\logs" mkdir "%DCA_DIR%\logs"

REM ---- Dispatch -------------------------------------------------
set CMD=%~1
if /I "%CMD%"==""        goto :usage
if /I "%CMD%"=="help"    goto :usage
if /I "%CMD%"=="-h"      goto :usage
if /I "%CMD%"=="--help"  goto :usage
if /I "%CMD%"=="start"   goto :start
if /I "%CMD%"=="run"     goto :run
if /I "%CMD%"=="stop"    goto :stop
if /I "%CMD%"=="restart" goto :restart
if /I "%CMD%"=="open"    goto :open
if /I "%CMD%"=="status"  goto :status
if /I "%CMD%"=="logs"    goto :logs

echo [dca] Unknown command: %CMD%
goto :usage

REM ---------------------------------------------------------------
:usage
echo.
echo   Data Collection Agent — control script
echo   --------------------------------------------------
echo   Usage: dca ^<command^>
echo.
echo     start     start the server in a NEW window (close window to stop)
echo     run       run the server in THIS window (Ctrl+C to stop)
echo     stop      kill anything listening on port %DCA_PORT%
echo     restart   stop, then start
echo     open      open the agent gallery in your browser
echo     status    show whether the server is up
echo     logs      tail the log file (Ctrl+C to exit)
echo.
echo   Current settings:
echo     port       = %DCA_PORT%
echo     python     = %DCA_PYTHON%
echo     log file   = %DCA_LOG%
echo.
echo   Override with: set DCA_PORT=5099  ^|  set DCA_PYTHON=^<path^>
echo.
exit /b 0

REM ---------------------------------------------------------------
:start
call :is_running >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [dca] Server already running on port %DCA_PORT%.
    echo [dca]   - Gallery: %DCA_URL_GALLERY%
    echo [dca]   - Builder: %DCA_URL_BUILDER%
    echo [dca]   - Example: %DCA_URL_EXAMPLE%
    exit /b 0
)
echo [dca] Starting standalone server on port %DCA_PORT%...
echo [dca] A new window will open with the running server. Close it to stop,
echo [dca] or run "dca stop" from another shell.
REM Launch python in a fresh visible window. From an interactive cmd.exe,
REM "start <title>" creates a new process group that survives this batch
REM exiting. The window stays open while the server runs; closing it (or
REM Ctrl+C inside it) stops the server. Module-level logs still go to
REM logs/data_collection_*_log.txt regardless of this window.
start "DCA Server (close window to stop)" "%DCA_PYTHON%" "%DCA_RUNNER%"
REM Give Flask a moment to bind
call :wait_for_start
call :is_running >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [dca] Server is up.
    echo [dca]   - Gallery: %DCA_URL_GALLERY%
    echo [dca]   - Builder: %DCA_URL_BUILDER%
    echo [dca]   - Example: %DCA_URL_EXAMPLE%
) else (
    echo [dca] Server did not start within timeout. Check the log:
    echo [dca]   %DCA_LOG%
    exit /b 1
)
exit /b 0

REM ---------------------------------------------------------------
:run
REM Foreground mode: run the server in THIS window. Ctrl+C to stop.
REM This is the most reliable mode if `start` ever has issues — you see
REM all output directly and Ctrl+C kills it cleanly.
echo [dca] Running standalone server in this window on port %DCA_PORT%.
echo [dca] Press Ctrl+C to stop. (Or close this window.)
echo.
"%DCA_PYTHON%" "%DCA_RUNNER%"
exit /b %ERRORLEVEL%

REM ---------------------------------------------------------------
:stop
echo [dca] Looking for processes listening on port %DCA_PORT%...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%DCA_PORT% " ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo [dca] Killing PID %%a
        taskkill /F /PID %%a >nul 2>nul
        set FOUND=1
    )
)
if %FOUND%==0 (
    echo [dca] Nothing listening on port %DCA_PORT%.
) else (
    echo [dca] Stopped.
)
exit /b 0

REM ---------------------------------------------------------------
:restart
REM Run stop/start as fresh batch invocations to avoid cmd's "cannot find
REM label" issue when chaining call ^:stop -> call ^:start in one process.
cmd /c ""%~f0" stop"
cmd /c ""%~f0" start"
exit /b %ERRORLEVEL%

REM ---------------------------------------------------------------
:open
REM Avoid parenthesized blocks here so we can rely on the runtime errorlevel
REM (cmd expands %ERRORLEVEL% inside ( ) blocks at parse time, not at runtime).
call :is_running >nul 2>nul
if %ERRORLEVEL%==0 goto :open_browser
echo [dca] Server isn't running. Starting it first...
call :start
if errorlevel 1 exit /b 1
:open_browser
echo [dca] Opening %DCA_URL_GALLERY% in your default browser...
start "" "%DCA_URL_GALLERY%"
exit /b 0

REM ---------------------------------------------------------------
:status
call :is_running >nul 2>nul
if %ERRORLEVEL%==0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%DCA_PORT% " ^| findstr "LISTENING"') do (
        echo [dca] UP — listening on port %DCA_PORT% (PID %%a^)
        echo [dca]      Gallery: %DCA_URL_GALLERY%
        echo [dca]      Builder: %DCA_URL_BUILDER%
        echo [dca]      Example: %DCA_URL_EXAMPLE%
        exit /b 0
    )
) else (
    echo [dca] DOWN — nothing listening on port %DCA_PORT%.
    exit /b 1
)
exit /b 0

REM ---------------------------------------------------------------
:logs
if not exist "%DCA_LOG%" (
    echo [dca] No log file yet at %DCA_LOG%
    exit /b 0
)
echo [dca] Tailing %DCA_LOG%   (Ctrl+C to exit)
echo.
powershell -NoProfile -Command "Get-Content -LiteralPath '%DCA_LOG%' -Wait -Tail 50"
exit /b 0

REM ---------------------------------------------------------------
REM  Helper: returns 0 if something is listening on DCA_PORT, else 1
:is_running
netstat -ano | findstr ":%DCA_PORT% " | findstr "LISTENING" >nul 2>nul
exit /b %ERRORLEVEL%

REM Helper: poll the port until something binds, or timeout (~30s).
REM The platform's import chain (secure_config, model_overrides, langchain)
REM can take ~5-10s on first import, so give Flask plenty of time.
:wait_for_start
set /a _tries=0
:wait_loop
set /a _tries+=1
if %_tries% GTR 30 exit /b 1
call :is_running >nul 2>nul
if %ERRORLEVEL%==0 exit /b 0
REM 1s sleep — `timeout /t 1` is the standard cmd idiom. /nobreak prevents
REM the user accidentally skipping with a keypress.
timeout /t 1 /nobreak >nul 2>nul
goto :wait_loop
