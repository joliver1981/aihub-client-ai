@echo off
rem AI Hub Scenario Console - day-in-the-life scenario operations on :7742
rem Port 7742 is clear of the AI Hub range (5001-5111), builder (8100),
rem demo control panel (3100), portal server (3000) and SFTP server (2222).
cd /d "%~dp0"
set PY=C:\Users\james\miniconda3\envs\aihub2.1\python.exe
start "" http://localhost:7742
"%PY%" console.py
