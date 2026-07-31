@echo off
rem Meridian Supply Co. demo portal (2FA) - login/TOTP/downloads fixture on :3000
cd /d "%~dp0"
set PY=C:\Users\james\miniconda3\envs\aihub2.1\python.exe
"%PY%" make_fixtures.py
"%PY%" portal_server.py
