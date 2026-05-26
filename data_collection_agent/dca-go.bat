@echo off
REM ============================================================
REM  dca-go.bat — one-shot launch
REM
REM  1. stop any stale server on the configured port
REM  2. start a fresh server in a new window
REM  3. open the agent gallery in your default browser
REM
REM  Wraps dca.bat — environment overrides (DCA_PORT, DCA_PYTHON)
REM  are honored automatically.
REM ============================================================

setlocal
set BATCH_DIR=%~dp0
if "%BATCH_DIR:~-1%"=="\" set BATCH_DIR=%BATCH_DIR:~0,-1%

if not exist "%BATCH_DIR%\dca.bat" (
    echo [dca-go] dca.bat not found next to this script: %BATCH_DIR%\dca.bat
    exit /b 1
)

echo.
echo [dca-go] Stopping any existing server...
cmd /c ""%BATCH_DIR%\dca.bat" stop"

echo.
echo [dca-go] Starting fresh server...
cmd /c ""%BATCH_DIR%\dca.bat" start"
if errorlevel 1 (
    echo.
    echo [dca-go] Server didn't come up. Try running directly:
    echo [dca-go]    dca run
    echo [dca-go] to see the error in this window.
    exit /b 1
)

echo.
echo [dca-go] Opening agent gallery in your browser...
cmd /c ""%BATCH_DIR%\dca.bat" open"

echo.
echo [dca-go] Done. Use "dca status" to check, "dca stop" to kill the server.
endlocal
