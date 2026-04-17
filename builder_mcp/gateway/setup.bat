@echo off
REM MCP Gateway Service Setup Script
REM Creates virtual environment and installs dependencies

echo ==========================================
echo  MCP Gateway Service Setup
echo ==========================================

set GATEWAY_DIR=%~dp0

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python is not installed or not in PATH
    exit /b 1
)

REM Create virtual environment
echo.
echo Creating virtual environment...
cd /d "%GATEWAY_DIR%"
python -m venv venv
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to create virtual environment
    exit /b 1
)

REM Activate and install
echo Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install dependencies
    exit /b 1
)

echo.
echo ==========================================
echo  Setup Complete!
echo ==========================================
echo.
echo To start the gateway:
echo   cd %GATEWAY_DIR%
echo   venv\Scripts\activate
echo   python app_mcp_gateway.py
echo.
echo Or register as a Windows service with NSSM:
echo   nssm install AIHub_MCP_Gateway "%GATEWAY_DIR%venv\Scripts\python.exe" "%GATEWAY_DIR%app_mcp_gateway.py"
echo   nssm set AIHub_MCP_Gateway AppDirectory "%GATEWAY_DIR%"
echo   nssm start AIHub_MCP_Gateway
echo.
