@echo off
rem AI Hub Demo Control Panel - demo pre-flight / start / reset console on :3100
cd /d "%~dp0"
set PY=C:\Users\james\miniconda3\envs\aihub2.1\python.exe
start "" http://localhost:3100
"%PY%" control_panel.py
