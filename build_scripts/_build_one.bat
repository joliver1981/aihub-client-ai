@echo off
:: ===========================================================================
:: _build_one.bat - shared engine for the per-service build scripts
:: ===========================================================================
:: Standalone: does NOT call Build_AIHub_Executables_OneDir_Dev_v4.bat, so a
:: single-service build can run without touching the full-build script.
::
:: Called by the build_<service>.bat wrappers in this folder:
::     _build_one.bat <shortname> [FULL]
::
:: FAST (default) omits --clean so PyInstaller reuses its cached analysis.
:: FULL passes --clean for a cold rebuild of that one target.
::
:: NOTE ON DRIFT: the env/spec table below duplicates the one in
:: Build_AIHub_Executables_OneDir_Dev_v4.bat. If a service changes env or spec
:: path, update BOTH. Keeping this standalone was the explicit tradeoff.
:: ===========================================================================
setlocal

SET "CONDA_PATH=C:\Users\james\miniconda3"
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

SET "TARGET=%~1"
SET "CLEANFLAG="
if /I "%~2"=="FULL" SET "CLEANFLAG=--clean"

if "%TARGET%"=="" (
    echo ERROR: no target given. Use one of:
    echo   app docapi docjob jss vector agentapi knowledge executor
    echo   mcp builder builderdata cloudgw cc browseruse agent
    exit /b 1
)

:: --- env + spec + output per target ---------------------------------------
SET "B_ENV="
SET "B_SPEC="
SET "B_OUT="
if /I "%TARGET%"=="app"         ( SET "B_ENV=aihub2.1"          & SET "B_SPEC=app_onedir.spec"                                          & SET "B_OUT=app" )
if /I "%TARGET%"=="docapi"      ( SET "B_ENV=aihubant"          & SET "B_SPEC=wsgi_doc_api_onedir.spec"                                 & SET "B_OUT=document_api_server" )
if /I "%TARGET%"=="docjob"      ( SET "B_ENV=aihubant"          & SET "B_SPEC=app_doc_job_q_onedir.spec"                                & SET "B_OUT=document_job_processor" )
if /I "%TARGET%"=="jss"         ( SET "B_ENV=jss"               & SET "B_SPEC=app_jss_main_onedir.spec"                                 & SET "B_OUT=job_scheduler_service" )
if /I "%TARGET%"=="vector"      ( SET "B_ENV=aihubvector2"      & SET "B_SPEC=wsgi_vector_api_onedir.spec"                              & SET "B_OUT=wsgi_vector_api" )
if /I "%TARGET%"=="agentapi"    ( SET "B_ENV=aihub2.1"          & SET "B_SPEC=wsgi_agent_api_onedir.spec"                               & SET "B_OUT=wsgi_agent_api" )
if /I "%TARGET%"=="knowledge"   ( SET "B_ENV=aihub2.1"          & SET "B_SPEC=wsgi_knowledge_api_onedir.spec"                           & SET "B_OUT=wsgi_knowledge_api" )
if /I "%TARGET%"=="executor"    ( SET "B_ENV=aihub2.1"          & SET "B_SPEC=wsgi_executor_service_onedir.spec"                        & SET "B_OUT=wsgi_executor_service" )
if /I "%TARGET%"=="mcp"         ( SET "B_ENV=aihubmcp"          & SET "B_SPEC=builder_mcp\gateway\app_mcp_gateway_onedir.spec"          & SET "B_OUT=mcp_gateway" )
if /I "%TARGET%"=="builder"     ( SET "B_ENV=aihubbuilder"      & SET "B_SPEC=builder_service\builder_service_onedir.spec"              & SET "B_OUT=builder_service" )
if /I "%TARGET%"=="builderdata" ( SET "B_ENV=aihubbuilder"      & SET "B_SPEC=builder_data\builder_data_onedir.spec"                    & SET "B_OUT=builder_data" )
if /I "%TARGET%"=="cloudgw"     ( SET "B_ENV=aihubcloudgateway" & SET "B_SPEC=builder_cloud\gateway\app_cloud_gateway_onedir.spec"      & SET "B_OUT=cloud_gateway" )
if /I "%TARGET%"=="cc"          ( SET "B_ENV=aihubbuilder"      & SET "B_SPEC=command_center_service\command_center_service_onedir.spec" & SET "B_OUT=command_center_service" )

echo ============================================================
echo  Build single service: %TARGET%
if not "%CLEANFLAG%"=="" echo  Mode: FULL ^(--clean^)
if "%CLEANFLAG%"=="" echo  Mode: FAST ^(cached analysis^)
echo ============================================================

:: --- non-PyInstaller targets ----------------------------------------------
if /I "%TARGET%"=="agent"      goto :AGENT
if /I "%TARGET%"=="browseruse" goto :BROWSERUSE

if "%B_SPEC%"=="" (
    echo ERROR: unknown target "%TARGET%".
    echo Valid: app docapi docjob jss vector agentapi knowledge executor
    echo        mcp builder builderdata cloudgw cc browseruse agent
    exit /b 1
)

:: --- PyInstaller build ------------------------------------------------------
:: _build_config.py is regenerated because the exes compile it in. Skipping it
:: would bake whatever creds were last generated into this one service.
echo [PRE] Regenerating _build_config.py...
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python scripts\generate_build_config.py
if %ERRORLEVEL% NEQ 0 ( echo ERROR: generate_build_config.py failed! & exit /b 1 )

echo [BUILD] %B_SPEC% in env %B_ENV% ...
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\%B_ENV%"
cd /d %PROJECT_PATH%
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller %B_SPEC% %CLEANFLAG% --noconfirm
if %ERRORLEVEL% NEQ 0 ( echo ERROR: %B_SPEC% build FAILED! & exit /b 1 )
echo [OK] dist\%B_OUT%\
goto :FRESHNESS

:: --- The Agent (source + isolated env) -------------------------------------
:AGENT
echo [BUILD] The Agent via scripts\build_agent_service.ps1 ...
SET "AGENTARGS=-SkipEnv"
if not "%CLEANFLAG%"=="" SET "AGENTARGS="
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_PATH%\scripts\build_agent_service.ps1" -Repo "%PROJECT_PATH%" %AGENTARGS%
if %ERRORLEVEL% NEQ 0 ( echo ERROR: build_agent_service.ps1 FAILED! & exit /b 1 )
if not exist "%PROJECT_PATH%\dist\agent_service\main.py" ( echo ERROR: dist\agent_service\main.py missing! & exit /b 1 )
echo [OK] dist\agent_service\  ^(FULL also refreshes dist\agent_env\^)
goto :FRESHNESS

:: --- Browser Use (source + env + chromium) ---------------------------------
:BROWSERUSE
echo [BUILD] Browser Use source + env + chromium ...
robocopy "%PROJECT_PATH%\browser_use_service" "%PROJECT_PATH%\dist\browser_use_service" /MIR /XD _scratch __pycache__ .pytest_cache /XF *.log /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 ( echo ERROR: browser_use_service copy failed! & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_PATH%\scripts\stage_cc_tools_subset.ps1" -Dest "%PROJECT_PATH%\dist\browser_use_service" -Repo "%PROJECT_PATH%"
if %ERRORLEVEL% NEQ 0 ( echo ERROR: command_center.tools staging failed! & exit /b 1 )
:: env + chromium only on FULL - they are large and rarely change
if not "%CLEANFLAG%"=="" (
    robocopy "%CONDA_PATH%\envs\aihub-browseruse" "%PROJECT_PATH%\dist\browser_use_env" /MIR /NFL /NDL /NJH /NJS /NP
    if !ERRORLEVEL! GEQ 8 ( echo ERROR: browser_use_env copy failed! & exit /b 1 )
    robocopy "%LOCALAPPDATA%\ms-playwright\chromium-1223" "%PROJECT_PATH%\dist\browser_use_chromium\chromium-1223" /MIR /NFL /NDL /NJH /NJS /NP
    if !ERRORLEVEL! GEQ 8 ( echo ERROR: browser_use_chromium copy failed! & exit /b 1 )
) else (
    echo   [skip] env + chromium ^(pass FULL to refresh them^)
)
cmd /c exit 0
echo [OK] dist\browser_use_service\
goto :FRESHNESS

:: --- freshness -------------------------------------------------------------
:FRESHNESS
echo.
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python scripts\check_dist_freshness.py
echo.
echo Reminder: a single-service build leaves the OTHER dist trees as they were.
echo Compile the installer only when the freshness report above is acceptable.
exit /b 0
