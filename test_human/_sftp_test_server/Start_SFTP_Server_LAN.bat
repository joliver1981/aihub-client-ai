@echo off
rem ============================================================================
rem  Start_SFTP_Server_LAN.bat - start the SFTP/FTP/FTPS test rig bound to
rem  0.0.0.0 so an INSTALLED box on the LAN (e.g. 10.0.0.6) can dial back to it.
rem
rem  WHY: the regression suite's File Transfer checks (pack 14, run inside pack
rem  15 --host <box>) make the ENGINE on the target upload to this machine. With
rem  the default loopback bind (Start_SFTP_Server.bat) that connection is
rem  refused, and the check used to read as a File Transfer failure. Pack 14 now
rem  probes the LAN address first and SKIPs with a reason when the rig is not
rem  reachable there - this script is how you make it reachable.
rem
rem  This is a throwaway fixture (testuser/testpass, cleartext-capable FTP).
rem  Binding off-loopback exposes it to the LAN: use it on the trusted test LAN
rem  only, and close the window when the run is done.
rem
rem  Firewall (one-time, elevated prompt - this script only CHECKS for it):
rem    netsh advfirewall firewall add rule name="AIHub SFTP test rig" dir=in ^
rem        action=allow protocol=TCP localport=2222,2121,60000-60099
rem ============================================================================
title AI Hub SFTP/FTP test server (LAN 0.0.0.0:2222)
setlocal

set "PYEXE=C:\Users\james\miniconda3\envs\testftp\python.exe"
set "SFTP_TEST_HOST=0.0.0.0"

if not exist "%PYEXE%" (
    echo [ERROR] testftp environment python not found: %PYEXE%
    echo         conda create -n testftp python=3.11 -y ^&^& %PYEXE% -m pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)

netstat -ano | findstr /r /c:":2222 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Something already LISTENS on 2222. If that is the LOOPBACK rig
    echo        (Start_SFTP_Server.bat), close it first - a remote engine cannot
    echo        reach 127.0.0.1 on this machine.
    pause
    exit /b 0
)

netsh advfirewall firewall show rule name="AIHub SFTP test rig" >nul 2>&1
if errorlevel 1 (
    echo [WARN] No inbound firewall rule named "AIHub SFTP test rig" was found.
    echo        A remote engine box will be blocked on 2222/2121 until you add it
    echo        from an ELEVATED prompt:
    echo        netsh advfirewall firewall add rule name="AIHub SFTP test rig" dir=in action=allow protocol=TCP localport=2222,2121,60000-60099
    echo.
)

cd /d "%~dp0"
echo Starting the SFTP/FTP/FTPS test server on 0.0.0.0 (Ctrl+C to stop)...
echo.
"%PYEXE%" run_all.py

echo.
echo Server stopped (exit code %errorlevel%).
pause
endlocal
