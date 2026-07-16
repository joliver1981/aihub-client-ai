@echo off
rem ============================================================================
rem  Start_SFTP_Server.bat - double-click to start the local SFTP/FTP/FTPS
rem  test server used by the human test packs (08 Automations Studio,
rem  09 Code Flows) and the CC transfer-tool tests.
rem
rem  Serves:  sftp://testuser:testpass@127.0.0.1:2222   (+ FTP/FTPS on 2121)
rem  Matches the Local Secret AUTODEMO_SFTP already seeded in the app.
rem  Leave this window open while testing; press Ctrl+C (or close it) to stop.
rem ============================================================================
title AI Hub SFTP/FTP test server (127.0.0.1:2222)
setlocal

set "PYEXE=C:\Users\james\miniconda3\envs\testftp\python.exe"

if not exist "%PYEXE%" (
    echo [ERROR] testftp environment python not found:
    echo         %PYEXE%
    echo.
    echo Create it once with:
    echo         conda create -n testftp python=3.11 -y
    echo         %PYEXE% -m pip install -r "%~dp0requirements.txt"
    echo.
    pause
    exit /b 1
)

rem Friendly check: is the SFTP port already serving? (server already running)
netstat -ano | findstr /r /c:":2222 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Something is already LISTENING on port 2222 - the test server
    echo        is probably already running in another window. Nothing to do.
    echo.
    pause
    exit /b 0
)

cd /d "%~dp0"
echo Starting the SFTP/FTP/FTPS test server (Ctrl+C to stop)...
echo.
"%PYEXE%" run_all.py

rem If the server exited on its own (error, port clash), keep the window open
rem so the message is readable.
echo.
echo Server stopped (exit code %errorlevel%).
pause
endlocal
