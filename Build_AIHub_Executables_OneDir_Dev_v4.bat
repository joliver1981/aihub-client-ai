@echo off
setlocal EnableDelayedExpansion
echo ============================================================
echo  AI Hub OneDir Build Process (AI-DEV) v4
echo  Creates folder-based executables (AV-friendly)
echo  Source: C:\src\aihub-client-ai-dev
echo  Services: 15 total (13 PyInstaller + Browser Use + The Agent)
echo ============================================================
echo.
echo  CHANGES vs v3
echo    1. BUILDS THE AGENT. v3 never did - dist\agent_service was only ever
echo       refreshed by running scripts\build_agent_service.ps1 by hand, so an
echo       installer built from v3 shipped whatever agent build happened to be
echo       sitting in dist\ (2026-09-06: two days stale, missing search_tables
echo       and delete_skill). Now step [15], always.
echo    2. FAST mode (default): no --clean, so PyInstaller reuses its cached
echo       analysis. Use FULL for a cold, guaranteed-from-scratch build.
echo    3. ONLY  mode: build one target by name for a quick iteration.
echo    4. Freshness report at the end: every dist\ tree older than its source
echo       is listed loudly, so a stale artifact cannot ship silently.
echo.
echo  USAGE
echo    Build_AIHub_Executables_OneDir_Dev_v4.bat              (fast, all)
echo    Build_AIHub_Executables_OneDir_Dev_v4.bat FULL         (--clean, all)
echo    Build_AIHub_Executables_OneDir_Dev_v4.bat ONLY agent   (agent only)
echo    Build_AIHub_Executables_OneDir_Dev_v4.bat ONLY app     (main app only)
echo.
echo  BEFORE YOU SPEED THIS UP FURTHER - read the note at the bottom of this
echo  file about UPX and antivirus exclusions. Those are the two big wins and
echo  neither one lives in this script.
echo.

SET "CONDA_PATH=C:\Users\james\miniconda3"
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

:: ---------------------------------------------------------------------------
:: Mode handling
:: ---------------------------------------------------------------------------
SET "CLEANFLAG="
SET "ONLYTARGET="
if /I "%~1"=="FULL" (
    SET "CLEANFLAG=--clean"
    echo [MODE] FULL - cold build, PyInstaller cache discarded
) else if /I "%~1"=="ONLY" (
    SET "ONLYTARGET=%~2"
    echo [MODE] ONLY - building target: %~2
) else (
    echo [MODE] FAST - reusing PyInstaller cache ^(pass FULL for a cold build^)
)
echo.

:: ---------------------------------------------------------------------------
:: [PRE] build config - always, cheap, and everything downstream depends on it
:: ---------------------------------------------------------------------------
if not "%ONLYTARGET%"=="" goto :skip_pre
echo [PRE] Generating _build_config.py from .env...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python scripts\generate_build_config.py
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: generate_build_config.py failed! Aborting build.
    pause
    exit /b 1
)
if not exist "%PROJECT_PATH%\_build_config.py" (
    echo ERROR: _build_config.py was not created. Aborting build.
    pause
    exit /b 1
)
if not exist "%PROJECT_PATH%\_build_config_client.py" (
    echo ERROR: _build_config_client.py was not created. Aborting build.
    pause
    exit /b 1
)
echo [PRE] Complete
:skip_pre

:: ---------------------------------------------------------------------------
:: PyInstaller targets.  CALL :BUILD <label> <env> <spec> <outdir> <shortname>
:: ---------------------------------------------------------------------------
CALL :BUILD "1/13"  aihub2.1          "app_onedir.spec"                                      "app"                    app
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "2/13"  aihubant          "wsgi_doc_api_onedir.spec"                             "document_api_server"    docapi
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "3/13"  aihubant          "app_doc_job_q_onedir.spec"                            "document_job_processor" docjob
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "4/13"  jss               "app_jss_main_onedir.spec"                             "job_scheduler_service"  jss
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "5/13"  aihubvector2      "wsgi_vector_api_onedir.spec"                          "wsgi_vector_api"        vector
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "6/13"  aihub2.1          "wsgi_agent_api_onedir.spec"                           "wsgi_agent_api"         agentapi
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "7/13"  aihub2.1          "wsgi_knowledge_api_onedir.spec"                       "wsgi_knowledge_api"     knowledge
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "8/13"  aihub2.1          "wsgi_executor_service_onedir.spec"                    "wsgi_executor_service"  executor
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "9/13"  aihubmcp          "builder_mcp\gateway\app_mcp_gateway_onedir.spec"      "mcp_gateway"            mcp
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "10/13" aihubbuilder      "builder_service\builder_service_onedir.spec"          "builder_service"        builder
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "11/13" aihubbuilder      "builder_data\builder_data_onedir.spec"                "builder_data"           builderdata
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "12/13" aihubcloudgateway "builder_cloud\gateway\app_cloud_gateway_onedir.spec"  "cloud_gateway"          cloudgw
if errorlevel 1 goto :BUILD_FAILED
CALL :BUILD "13/13" aihubbuilder      "command_center_service\command_center_service_onedir.spec" "command_center_service" cc
if errorlevel 1 goto :BUILD_FAILED

:: ---------------------------------------------------------------------------
:: [14] Browser Use (Strategy B: source + env + chromium)
:: ---------------------------------------------------------------------------
if not "%ONLYTARGET%"=="" if /I not "%ONLYTARGET%"=="browseruse" goto :skip_bu
echo.
echo ============================================================
echo  [14] Browser Use service (source + env + chromium)
echo ============================================================
echo [14] Copying browser_use_service source...
robocopy "%PROJECT_PATH%\browser_use_service" "%PROJECT_PATH%\dist\browser_use_service" /MIR /XD _scratch __pycache__ .pytest_cache /XF *.log /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 ( echo ERROR: browser_use_service source copy failed! & pause & exit /b 1 )

echo [14] Staging command_center.tools subset -^> dist\browser_use_service\command_center...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_PATH%\scripts\stage_cc_tools_subset.ps1" -Dest "%PROJECT_PATH%\dist\browser_use_service" -Repo "%PROJECT_PATH%"
if %ERRORLEVEL% NEQ 0 ( echo ERROR: command_center.tools staging failed! & pause & exit /b 1 )

echo [14] Copying isolated env (aihub-browseruse) -^> dist\browser_use_env...
robocopy "%CONDA_PATH%\envs\aihub-browseruse" "%PROJECT_PATH%\dist\browser_use_env" /MIR /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 ( echo ERROR: browser_use_env copy failed! & pause & exit /b 1 )

echo [14] Copying bundled Chromium (chromium-1223)...
robocopy "%LOCALAPPDATA%\ms-playwright\chromium-1223" "%PROJECT_PATH%\dist\browser_use_chromium\chromium-1223" /MIR /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 ( echo ERROR: browser_use_chromium copy failed! & pause & exit /b 1 )
cmd /c exit 0
echo [14] Complete
:skip_bu

:: ---------------------------------------------------------------------------
:: [15] THE AGENT  <-- v3 NEVER DID THIS. This is the fix.
:: ---------------------------------------------------------------------------
if not "%ONLYTARGET%"=="" if /I not "%ONLYTARGET%"=="agent" goto :skip_agent
echo.
echo ============================================================
echo  [15] The Agent (source + isolated env aihub-agent)
echo ============================================================
:: -SkipEnv stages SOURCE ONLY and is fast. The env copy is large and only
:: changes when aihub-agent's packages change (an SDK bump, a new dependency).
:: FULL mode copies the env too; FAST/ONLY stage source only.
SET "AGENTARGS=-SkipEnv"
if /I "%~1"=="FULL" SET "AGENTARGS="
echo [15] Running build_agent_service.ps1 %AGENTARGS% ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_PATH%\scripts\build_agent_service.ps1" -Repo "%PROJECT_PATH%" %AGENTARGS%
if %ERRORLEVEL% NEQ 0 ( echo ERROR: build_agent_service.ps1 failed! & pause & exit /b 1 )
if not exist "%PROJECT_PATH%\dist\agent_service\main.py" (
    echo ERROR: dist\agent_service\main.py missing after staging! & pause & exit /b 1
)
echo [15] Complete - Output: dist\agent_service\ ^(+ dist\agent_env\ on FULL^)
:skip_agent

:: ---------------------------------------------------------------------------
:: [16] Staging refresh
:: ---------------------------------------------------------------------------
if not "%ONLYTARGET%"=="" goto :skip_stage
echo.
echo [16] Refreshing python-bundle-requirements staging...
robocopy "%PROJECT_PATH%\agent_environments\python-bundle-requirements" "%PROJECT_PATH%\dist\python-bundle-requirements" /MIR /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 ( echo ERROR: python-bundle-requirements copy failed! & pause & exit /b 1 )
cmd /c exit 0
:skip_stage

:: ---------------------------------------------------------------------------
:: [17] FRESHNESS REPORT - a stale artifact must never ship silently
:: ---------------------------------------------------------------------------
echo.
echo ============================================================
echo  [17] Freshness check - dist\ vs source
echo ============================================================
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python scripts\check_dist_freshness.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  *** ONE OR MORE DIST TREES ARE OLDER THAN THEIR SOURCE ***
    echo  Do NOT compile the installer until this is resolved.
    echo.
)

goto :DONE

:BUILD_FAILED
echo.
echo ============================================================
echo  BUILD FAILED - stopping here.
echo  The target above did NOT build. dist\ now holds a MIX of new and
echo  old artifacts - do NOT compile the installer from it.
echo  Fix the error and re-run; FAST mode skips work already done.
echo ============================================================
pause
exit /b 1

:DONE
echo.
echo ============================================================
echo  BUILD COMPLETE - OneDir (AI-DEV) v4
echo ============================================================
echo.
echo  AFTER INSTALLING, verify the exes actually shipped:
echo    curl -s http://TARGET:5111/health          -^> must contain "spawn"
echo  and the agent tool surface must include search_tables + delete_skill.
echo  A 10-second check that would have caught the 2026-09-06 stale install.
echo.
pause
goto :eof

:: ===========================================================================
:: :BUILD <label> <env> <spec> <outdir> <shortname>
:: ===========================================================================
:BUILD
SET "B_LABEL=%~1"
SET "B_ENV=%~2"
SET "B_SPEC=%~3"
SET "B_OUT=%~4"
SET "B_NAME=%~5"
if not "%ONLYTARGET%"=="" if /I not "%ONLYTARGET%"=="%B_NAME%" exit /b 0
echo.
echo [%B_LABEL%] Building %B_SPEC% with %B_ENV% environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\%B_ENV%"
cd /d %PROJECT_PATH%
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller %B_SPEC% %CLEANFLAG% --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: %B_SPEC% build failed!
    exit /b 1
)
echo [%B_LABEL%] Complete - Output: dist\%B_OUT%\
exit /b 0

:: ===========================================================================
:: NOT DONE HERE - the two biggest speedups live outside this script
:: ===========================================================================
:: 1. UPX. Every *_onedir.spec sets upx=True (app_onedir.spec:284,302 and the
::    same in the others). UPX compresses each binary and is commonly 30-50%%
::    of build wall-time. For a onedir install it buys nearly nothing: Inno
::    compresses the payload anyway. Setting upx=False in the specs is likely
::    the single largest win available. NOT changed here because it edits the
::    specs, not this script - test one spec first and compare timings.
::
:: 2. Antivirus. Defender real-time scanning inspects every temp file
::    PyInstaller writes and every DLL it copies, across 13 bundles. Excluding
::    C:\src\aihub-client-ai-dev\build, ...\dist and %CONDA_PATH%\envs from
::    real-time scanning often halves total build time. Machine policy, not a
::    script change.
::
:: 3. Parallelism. The 13 PyInstaller targets are independent and could run
::    3-4 at a time as PowerShell jobs. NOT done here on purpose: each build
::    would need its own --workpath to avoid sharing build\ cache, and mixing
::    parallelism with the FAST cache is exactly how a stale artifact ships.
::    Worth doing only once the freshness check in [17] is trusted.
