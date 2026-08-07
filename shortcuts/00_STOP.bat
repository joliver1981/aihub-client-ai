@echo off
echo AI Hub Services Manager (AI-DEV) - STOP Script
echo ==============================================
echo.

:: All stop logic lives in stop-aihub-services.ps1 (window-title kill, command-line
:: sweep, port-ownership kill, then VERIFY every service port is actually free).
:: Batch-embedded PowerShell quoting broke silently in the past - never inline it here.
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0stop-aihub-services.ps1"
exit /b %ERRORLEVEL%
