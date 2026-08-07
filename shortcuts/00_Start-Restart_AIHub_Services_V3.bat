@echo off
setlocal
echo AI Hub Services Manager (AI-DEV) - Start/Restart Script
echo ========================================================

cd /d "%~dp0"

:: ============================================================================
:: STOP PHASE - all stop logic lives in stop-aihub-services.ps1:
::   window-title kill + command-line sweep + PORT-OWNERSHIP kill, then a
::   VERIFY loop that only passes when every service port is actually FREE.
:: It also records the pre-stop owner PID of every port so the verify phase
:: below can prove the restart produced NEW processes.
:: Batch-embedded PowerShell quoting broke silently in the past (parse errors
:: swallowed by for /f) - never inline PowerShell in this file again.
:: ============================================================================
call "%~dp000_STOP.bat"
if errorlevel 1 (
    echo.
    echo  *** STOP PHASE FAILED - a service port is still owned by an old process.
    echo  *** NOT starting services on top of it - that is how stale code gets served.
    echo  *** See the survivors listed above. If a kill was ACCESS DENIED, re-run
    echo  *** this script from an elevated Administrator window.
    echo.
    pause
    exit /b 1
)

:: Set the path to the Anaconda/Miniconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"
:: Set the project folder path
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

echo.
echo Starting all AI Hub (AI-DEV) services...
echo.

:: Start services

echo [1/14] Starting Document API Server (wsgi_doc_api.py) in aihubant environment...
start "AIHub-DEV Document API" /D "%PROJECT_PATH%" cmd /k "color 0B && title AIHub-DEV Document API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubant && python wsgi_doc_api.py"

:: timeout /t 1 /nobreak >nul

echo [2/14] Starting Document Job Queue (app_doc_job_q.py) in aihubant environment...
start "AIHub-DEV Doc Job Queue" /D "%PROJECT_PATH%" cmd /k "color 0C && title AIHub-DEV Doc Job Queue && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubant && python app_doc_job_q.py"

:: timeout /t 1 /nobreak >nul

echo [3/14] Starting JSS Main Application (app_jss_main.py) in jss environment...
start "AIHub-DEV JSS Main" /D "%PROJECT_PATH%" cmd /k "color 0D && title AIHub-DEV JSS Main && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate jss && python app_jss_main.py"

:: timeout /t 1 /nobreak >nul

echo [4/14] Starting Vector API Server (wsgi_vector_api.py) in aihubvector2 environment...
start "AIHub-DEV Vector API" /D "%PROJECT_PATH%" cmd /k "color 0E && title AIHub-DEV Vector API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubvector2 && python wsgi_vector_api.py"

:: timeout /t 1 /nobreak >nul

echo [5/14] Starting Agent API Server (wsgi_agent_api.py) in aihub2.1 environment...
start "AIHub-DEV Agent API" /D "%PROJECT_PATH%" cmd /k "color 0F && title AIHub-DEV Agent API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_agent_api.py"

:: timeout /t 1 /nobreak >nul

echo [6/14] Starting Knowledge API Server (wsgi_knowledge_api.py) in aihub2.1 environment...
start "AIHub-DEV Knowledge API" /D "%PROJECT_PATH%" cmd /k "color 0F && title AIHub-DEV Knowledge API && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_knowledge_api.py"

:: timeout /t 1 /nobreak >nul

echo [7/14] Starting Main Application (wsgi.py) in aihub2.1 environment...
start "AIHub-DEV Main App" /D "%PROJECT_PATH%" cmd /k "color 0A && title AIHub-DEV Main App && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi.py"

:: timeout /t 1 /nobreak >nul

echo [8/14] Starting Executor Service (wsgi_executor_service.py) in aihub2.1 environment...
start "AIHub-DEV Executor App" /D "%PROJECT_PATH%" cmd /k "color 0A && title AIHub-DEV Executor App && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub2.1 && python wsgi_executor_service.py"

:: timeout /t 1 /nobreak >nul

echo.
echo Waiting 5 seconds for MCP/Builder services to fully shut down...
:: timeout /t 5 /nobreak >nul
echo.

echo [9/14] Starting MCP Gateway (app_mcp_gateway.py) in aihubmcp environment...
start "AIHub-DEV MCP Gateway" /D "%PROJECT_PATH%\builder_mcp\gateway" cmd /k "color 03 && title AIHub-DEV MCP Gateway && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubmcp && python app_mcp_gateway.py"

:: timeout /t 1 /nobreak >nul

echo [10/14] Starting Cloud Storage Gateway (app_cloud_gateway.py) in aihubcloudgateway environment...
start "AIHub-DEV Cloud Gateway" /D "%PROJECT_PATH%\builder_cloud\gateway" cmd /k "color 09 && title AIHub-DEV Cloud Gateway && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubcloudgateway && python app_cloud_gateway.py"

:: timeout /t 1 /nobreak >nul

echo [11/14] Starting Builder Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Builder Service" /D "%PROJECT_PATH%\builder_service" cmd /k "color 06 && title AIHub-DEV Builder Service && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"

:: timeout /t 1 /nobreak >nul

echo [12/14] Starting Builder Data Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Builder Data" /D "%PROJECT_PATH%\builder_data" cmd /k "color 05 && title AIHub-DEV Builder Data && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"

:: timeout /t 1 /nobreak >nul

echo [13/14] Starting Command Center Service (main.py) in aihubbuilder environment...
start "AIHub-DEV Command Center" /D "%PROJECT_PATH%\command_center_service" cmd /k "color 0D && title AIHub-DEV Command Center && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihubbuilder && python main.py"

echo [14/14] Starting Browser Use Service (main.py) in aihub-browseruse environment...
start "AIHub-DEV Browser Use" /D "%PROJECT_PATH%\browser_use_service" cmd /k "color 04 && title AIHub-DEV Browser Use && call "%CONDA_PATH%\Scripts\activate.bat" && conda activate aihub-browseruse && python main.py"


:: ============================================================================
:: VERIFY PHASE - wait for every service port to be re-bound and prove each
:: listener is a NEW process (creation time after the stop phase completed).
:: Catches the exact failure this script used to hide: a stale process keeping
:: the port while the fresh service silently fails to bind.
:: ============================================================================
echo.
echo Verifying every service came up as a NEW process...
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0verify-aihub-services.ps1"
if errorlevel 1 (
    echo.
    echo  *** VERIFY FAILED - see the table above. A service is missing or STALE.
    echo  *** A stale listener means the app is serving OLD code right now.
    echo.
    pause
    exit /b 1
)

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
echo - Red    : Browser Use Service (aihub-browseruse) - port 5101
echo.
echo Source: %PROJECT_PATH%
echo.
echo To stop a service, close its window or press Ctrl+C in it.
echo To restart all services, run this script again.
echo.
REM pause
endlocal
