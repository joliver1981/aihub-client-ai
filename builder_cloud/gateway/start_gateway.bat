@echo off
REM Start Cloud Storage Gateway Service
REM Run this from the gateway directory

set GATEWAY_DIR=%~dp0
cd /d "%GATEWAY_DIR%"

REM Activate conda env if specified
if defined CLOUD_CONDA_ENV (
    call conda activate %CLOUD_CONDA_ENV%
) else (
    echo WARNING: CLOUD_CONDA_ENV not set. Using current Python environment.
)

REM Set default environment variables if not already set
if not defined CLOUD_GATEWAY_PORT set CLOUD_GATEWAY_PORT=5081
if not defined CLOUD_GATEWAY_LOG set CLOUD_GATEWAY_LOG=./logs/cloud_gateway_log.txt

echo Starting Cloud Storage Gateway on port %CLOUD_GATEWAY_PORT%...
python app_cloud_gateway.py
