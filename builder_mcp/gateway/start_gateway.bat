@echo off
REM Start MCP Gateway Service
REM Run this from the gateway directory

set GATEWAY_DIR=%~dp0
cd /d "%GATEWAY_DIR%"

REM Activate venv if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo WARNING: Virtual environment not found. Run setup.bat first.
    echo Attempting to start with system Python...
)

REM Set default environment variables if not already set
if not defined MCP_GATEWAY_PORT set MCP_GATEWAY_PORT=5071
if not defined MCP_GATEWAY_LOG set MCP_GATEWAY_LOG=./logs/mcp_gateway_log.txt

echo Starting MCP Gateway on port %MCP_GATEWAY_PORT%...
python app_mcp_gateway.py
