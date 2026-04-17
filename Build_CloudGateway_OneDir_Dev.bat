@echo off
echo ============================================================
echo  Cloud Storage Gateway - OneDir Build (AI-DEV)
echo  Source: C:\src\aihub-client-ai-dev
echo ============================================================
echo.

SET "CONDA_PATH=C:\Users\james\miniconda3"
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

CALL "%CONDA_PATH%\Scripts\activate.bat"

echo Building Cloud Storage Gateway with aihubcloudgateway environment...
echo --------------------------------------------------------
CALL conda activate aihubcloudgateway
cd /d %PROJECT_PATH%
pyinstaller builder_cloud/gateway/app_cloud_gateway_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Cloud Storage Gateway build failed!
    pause
    exit /b 1
)
CALL conda deactivate

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Output: %PROJECT_PATH%\dist\cloud_gateway\
echo ============================================================
echo.
pause
