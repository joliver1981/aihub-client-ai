@echo off
REM PredictiveForecast - Build Executable
REM Creates the PyInstaller onedir distribution

echo ============================================
echo  PredictiveForecast - Building Executable
echo ============================================

REM Activate conda environment if available
IF EXIST "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
    call "%USERPROFILE%\miniconda3\Scripts\activate.bat" fc
)

cd /d "%~dp0"

echo.
echo Building onedir distribution...
pyinstaller wsgi_forecast_onedir.spec --noconfirm

echo.
echo ============================================
echo  Build complete!
echo  Output: dist\predictive_forecast\
echo ============================================

pause
