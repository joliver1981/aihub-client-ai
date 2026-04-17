@echo off
setlocal enabledelayedexpansion
echo AI Hub Services Manager (AI-DEV) - Start/Restart Script
echo ========================================================

cd C:\src\aihub-client-ai-dev\shortcuts

call 00_STOP.bat

powershell -ExecutionPolicy Bypass -File kill-port-5091.ps1

powershell -ExecutionPolicy Bypass -File kill-port-8100.ps1

@echo off
set "PORT=5091"

:: Find the PID of the process listening on the specified port
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set "PID=%%a"
)

:: Check if a PID was found and kill it
if defined PID (
    echo Found process with PID %PID% on port %PORT%. Terminating...
    taskkill /F /PID %PID%
) else (
    echo No process found listening on port %PORT%.
)

:: Set the path to the Anaconda/Miniconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"
:: Set the project folder path
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

echo.
echo Checking for running services...
echo.

:: STEP 1: Kill by window title FIRST (while titles still say "AIHub-DEV*")
:: This kills both the cmd window AND its child python process in one shot.
:: Must happen BEFORE killing python.exe, because killing python causes cmd /k
:: to drop to a prompt where conda hooks change the window title.
echo Killing AIHub-DEV windows by title...
taskkill /FI "WINDOWTITLE eq AIHub-DEV*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Administrator:*AIHub-DEV*" /F >nul 2>&1

:: STEP 2: PowerShell window-title kill (catches windows taskkill might miss)
echo Using PowerShell to find remaining AIHub-DEV windows...
for /f %%P in ('powershell -NoProfile -Command "Get-Process | Where-Object { $_.MainWindowTitle -like ''AIHub-DEV*'' } | Select-Object -ExpandProperty Id"') do (
    echo [!] Found AIHub window PID %%P - killing process tree...
    taskkill /PID %%P /T /F >nul 2>&1
)

:: Brief pause to let windows close before orphan cleanup
timeout /t 2 /nobreak >nul

:: STEP 3: Kill any orphaned python processes (safety net for processes whose windows were already closed)
echo Cleaning up any orphaned python processes...
for /f %%P in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq ''python.exe'' -and ($_.CommandLine -match ''wsgi\.py'' -or $_.CommandLine -match ''wsgi_doc_api\.py'' -or $_.CommandLine -match ''app_doc_job_q\.py'' -or $_.CommandLine -match ''app_jss_main\.py'' -or $_.CommandLine -match ''wsgi_vector_api\.py'' -or $_.CommandLine -match ''wsgi_agent_api\.py'' -or $_.CommandLine -match ''wsgi_knowledge_api\.py'' -or $_.CommandLine -match ''wsgi_executor_service\.py'' -or $_.CommandLine -match ''app_mcp_gateway\.py'' -or $_.CommandLine -match ''app_cloud_gateway\.py'' -or $_.CommandLine -match ''builder_service\\\\main\.py'' -or $_.CommandLine -match ''builder_data\\\\main\.py'' -or $_.CommandLine -match ''command_center_service\\\\main\.py'') } | Select-Object -ExpandProperty ProcessId"') do (
    echo [!] Killing orphaned python PID %%P ...
    taskkill /PID %%P /T /F >nul 2>&1
)

:: Wait for processes to terminate
echo.
echo Waiting for processes to terminate...
timeout /t 2 /nobreak >nul

echo.
echo Starting all AI Hub (AI-DEV) services...
echo.

:: Start services

echo [1/13] Starting Document API Server (wsgi_doc_api.py) in aihubant environment...
start "AIHub-DEV Document API" /D "%PROJECT_PATH%" cmd /k "color 0B && title AIHub-DEV Document API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubant && python wsgi_doc_api.py"

:: timeout /t 1 /nobreak >nul

echo [2/13] Starting Document Job Queue (app_doc_job_q.py) in aihubant environment...
start "AIHub-DEV Doc Job Queue" /D "%PROJECT_PATH%" cmd /k "color 0C && title AIHub-DEV Doc Job Queue && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubant && python app_doc_job_q.py"

:: timeout /t 1 /nobreak >nul

echo [3/13] Starting JSS Main Application (app_jss_main.py) in jss environment...
start "AIHub-DEV JSS Main" /D "%PROJECT_PATH%" cmd /k "color 0D && title AIHub-DEV JSS Main && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate jss && python app_jss_main.py"

:: timeout /t 1 /nobreak >nul

echo [4/13] Starting Vector API Server (wsgi_vector_api.py) in aihubvector2 environment...
start "AIHub-DEV Vector API" /D "%PROJECT_PATH%" cmd /k "color 0E && title AIHub-DEV Vector API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubvector2 && python wsgi_vector_api.py"

:: timeout /t 1 /nobreak >nul

echo [5/13] Starting Agent API Server (wsgi_agent_api.py) in aihub2.1 environment...
start "AIHub-DEV Agent API" /D "%PROJECT_PATH%" cmd /k "color 0F && title AIHub-DEV Agent API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_agent_api.py"

:: timeout /t 1 /nobreak >nul

echo [6/13] Starting Knowledge API Server (wsgi_knowledge_api.py) in aihub2.1 environment...
start "AIHub-DEV Knowledge API" /D "%PROJECT_PATH%" cmd /k "color 0F && title AIHub-DEV Knowledge API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_knowledge_api.py"

:: timeout /t 1 /nobreak >nul

echo [7/13] Starting Main Application (wsgi.py) in aihub2.1 environment...
start "AIHub-DEV Main App" /D "%PROJECT_PATH%" cmd /k "color 0A && title AIHub-DEV Main App && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi.py"

:: timeout /t 1 /nobreak >nul

echo [8/13] Starting Executor Service (wsgi_executor_service.py) in aihub2.1 environment...
start "AIHub-DEV Executor App" /D "%PROJECT_PATH%" cmd /k "color 0A && title AIHub-DEV Executor App && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_executor_service.py"

:: timeout /t 1 /nobreak >nul

echo.
echo Waiting 5 seconds for MCP/Builder services to fully shut down...
:: timeout /t 5 /nobreak >nul
echo.

echo [9/13] Starting MCP Gateway (app_mcp_gateway.py) in aihubmcp environment...
start "AIHub-DEV MCP Gateway" /D "%PROJECT_PATH%\builder_mcp\gateway" cmd /k "color 03 && title AIHub-DEV MCP Gateway && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubmcp && python app_mcp_gateway.py"

:: timeout /t 1 /nobreak >nul

echo [10/13] Starting Cloud Storage Gateway (app_cloud_gateway.py) in aihubcloudgateway environment...
start "AIHub-DEV Cloud Gateway" /D "%PROJECT_PATH%\builder_cloud\gateway" cmd /k "color 09 && title AIHub-DEV Cloud Gateway && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubcloudgateway && python app_cloud_gateway.py"

:: timeout /t 1 /nobreak >nul

echo [11/13] Starting Builder Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Builder Service" /D "%PROJECT_PATH%\builder_service" cmd /k "color 06 && title AIHub-DEV Builder Service && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"

:: timeout /t 1 /nobreak >nul

echo [12/13] Starting Builder Data Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Builder Data" /D "%PROJECT_PATH%\builder_data" cmd /k "color 05 && title AIHub-DEV Builder Data && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"

:: timeout /t 1 /nobreak >nul

echo [13/13] Starting Command Center Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Command Center" /D "%PROJECT_PATH%\command_center_service" cmd /k "color 0D && title AIHub-DEV Command Center && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"


echo.
echo ============================================
echo All AI-DEV services have been launched!
echo ============================================
echo.
echo Each service is running in its own window:
echo - Green  : Main Application (aihub2.1)
echo - Cyan   : Document API Server (aihubant)
echo - Red    : Document Job Queue (aihubant)
echo - Purple : JSS Main Application (jss)
echo - Yellow : Vector API Server (aihubvector)
echo - White  : Agent API Server (aihub2.1)
echo - Aqua   : MCP Gateway (aihubmcp) - port 5071
echo - Blue   : Cloud Storage Gateway (aihubcloudgateway) - port 5081
echo - Brown  : Builder Service (aihubbuilder) - port 8100
echo - Magenta: Builder Data (aihubbuilder) - port 8200
echo - Purple : Command Center Service (aihubbuilder) - port 5091
echo.
echo Source: %PROJECT_PATH%
echo.
echo To stop a service, close its window or press Ctrl+C in it.
echo To restart all services, run this script again.
echo.
REM pause
endlocal
