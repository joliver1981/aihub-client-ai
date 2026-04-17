@echo off
REM PredictiveForecast - Development Server
REM Starts the Flask development server on port 5005

echo ============================================
echo  PredictiveForecast - Starting Dev Server
echo ============================================

REM Activate conda environment if available
IF EXIST "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
    call "%USERPROFILE%\miniconda3\Scripts\activate.bat" fc
)

cd /d "%~dp0"
python app.py

pause
