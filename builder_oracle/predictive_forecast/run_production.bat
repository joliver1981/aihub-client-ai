@echo off
REM PredictiveForecast - Production Server (Waitress)
REM Starts on port 5005 with 4 threads

echo ============================================
echo  PredictiveForecast - Production Server
echo ============================================

REM Activate conda environment if available
IF EXIST "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
    call "%USERPROFILE%\miniconda3\Scripts\activate.bat" fc
)

cd /d "%~dp0"
python wsgi.py

pause
