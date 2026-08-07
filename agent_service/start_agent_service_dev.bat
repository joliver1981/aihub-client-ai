@echo off
REM Dev start for The Agent service (port 5111, conda env aihub-agent).
REM Mirrors the V3 start script's per-service window pattern; standalone for A0
REM so the shared V3 script stays untouched until The Agent graduates.
SET "CONDA_PATH=C:\Users\james\miniconda3"
SET "SVC_PATH=C:\src\aihub-client-ai-dev\agent_service"
start "AIHub-DEV The Agent" /D "%SVC_PATH%" cmd /k "color 0B && title AIHub-DEV The Agent && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub-agent && python main.py"
