@echo off
echo ============================================================
echo  AI Hub OneDir Build Process (AI-DEV) v3
echo  Creates folder-based executables (AV-friendly)
echo  Source: C:\src\aihub-client-ai-dev
echo  Services: 13 total
echo ============================================================
echo.

:: Set the path to the Anaconda/Miniconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"

:: Set the project folder path - AI DEV version
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

:: =============================================================================
:: ENVIRONMENT ACTIVATION STRATEGY
:: Instead of relying on "conda activate <name>" (which can silently fail or
:: resolve to the wrong env), we activate each environment using the full path
:: to its activate.bat script. This guarantees the correct Python interpreter
:: and site-packages are used for every build step.
:: =============================================================================

echo.
echo [PRE] Generating _build_config.py from .env...
echo --------------------------------------------------------
:: Bundles the 24 allow-listed credential values into _build_config.py so
:: PyInstaller compiles them into client exes as bytecode (they never ship as
:: plaintext). .env on the build machine MUST contain the production creds.
:: _build_config.py is gitignored.
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python scripts\generate_build_config.py
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: generate_build_config.py failed! Aborting build.
    echo        Check that .env exists and contains the 24 credential values.
    pause
    exit /b 1
)
if not exist "%PROJECT_PATH%\_build_config.py" (
    echo ERROR: _build_config.py was not created. Aborting build.
    pause
    exit /b 1
)
echo [PRE] Complete - _build_config.py ready for bundling

echo.
echo [1/13] Building app_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -c "import openai; print('  openai:', openai.__version__)"
python -m PyInstaller app_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_onedir.spec build failed!
    pause
    exit /b 1
)
echo [1/13] Complete - Output: dist\app\

echo.
echo [2/13] Building wsgi_doc_api_onedir.spec with aihubant environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubant"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller wsgi_doc_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_doc_api_onedir.spec build failed!
    pause
    exit /b 1
)
echo [2/13] Complete - Output: dist\document_api_server\

echo.
echo [3/13] Building app_doc_job_q_onedir.spec with aihubant environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubant"
cd /d %PROJECT_PATH%
python -m PyInstaller app_doc_job_q_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_doc_job_q_onedir.spec build failed!
    pause
    exit /b 1
)
echo [3/13] Complete - Output: dist\document_job_processor\

echo.
echo [4/13] Building app_jss_main_onedir.spec with jss environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\jss"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller app_jss_main_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_jss_main_onedir.spec build failed!
    pause
    exit /b 1
)
echo [4/13] Complete - Output: dist\job_scheduler_service\

echo.
echo [5/13] Building wsgi_vector_api_onedir.spec with aihubvector2 environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubvector2"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller wsgi_vector_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_vector_api_onedir.spec build failed!
    pause
    exit /b 1
)
echo [5/13] Complete - Output: dist\wsgi_vector_api\

echo.
echo [6/13] Building wsgi_agent_api_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -c "import openai; print('  openai:', openai.__version__)"
python -m PyInstaller wsgi_agent_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_agent_api_onedir.spec build failed!
    pause
    exit /b 1
)
echo [6/13] Complete - Output: dist\wsgi_agent_api\

echo.
echo [7/13] Building wsgi_knowledge_api_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python -m PyInstaller wsgi_knowledge_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_knowledge_api_onedir.spec build failed!
    pause
    exit /b 1
)
echo [7/13] Complete - Output: dist\wsgi_knowledge_api\

echo.
echo [8/13] Building wsgi_executor_service_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihub2.1"
cd /d %PROJECT_PATH%
python -m PyInstaller wsgi_executor_service_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_executor_service_onedir.spec build failed!
    pause
    exit /b 1
)
echo [8/13] Complete - Output: dist\wsgi_executor_service\

echo.
echo ============================================================
echo  SERVICES - MCP Gateway, Builder Service, Builder Data,
echo             Cloud Storage Gateway, Command Center Service
echo ============================================================

echo.
echo [9/13] Building MCP Gateway with aihubmcp environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubmcp"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller builder_mcp/gateway/app_mcp_gateway_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: MCP Gateway build failed!
    pause
    exit /b 1
)
echo [9/13] Complete - Output: dist\mcp_gateway\

echo.
echo [10/13] Building Builder Service with aihubbuilder environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubbuilder"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller builder_service/builder_service_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Builder Service build failed!
    pause
    exit /b 1
)
echo [10/13] Complete - Output: dist\builder_service\

echo.
echo [11/13] Building Builder Data Service with aihubbuilder environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubbuilder"
cd /d %PROJECT_PATH%
python -m PyInstaller builder_data/builder_data_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Builder Data Service build failed!
    pause
    exit /b 1
)
echo [11/13] Complete - Output: dist\builder_data\

echo.
echo [12/13] Building Cloud Storage Gateway with aihubcloudgateway environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubcloudgateway"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller builder_cloud/gateway/app_cloud_gateway_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Cloud Storage Gateway build failed!
    pause
    exit /b 1
)
echo [12/13] Complete - Output: dist\cloud_gateway\

echo.
echo [13/13] Building Command Center Service with aihubbuilder environment...
echo --------------------------------------------------------
CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%\envs\aihubbuilder"
cd /d %PROJECT_PATH%
echo Verifying environment...
python -c "import sys; print('  Python:', sys.executable)"
python -m PyInstaller command_center_service/command_center_service_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Command Center Service build failed!
    pause
    exit /b 1
)
echo [13/13] Complete - Output: dist\command_center_service\

echo.
echo ============================================================
echo  BUILD COMPLETE - OneDir Structure (AI-DEV) v3
echo ============================================================
echo.
echo Output folders created in %PROJECT_PATH%\dist\:
echo   - app\                        (Main web application)
echo   - document_api_server\        (Document processing API)
echo   - document_job_processor\     (Document job queue)
echo   - job_scheduler_service\      (Job scheduler)
echo   - wsgi_vector_api\            (Vector search API)
echo   - wsgi_agent_api\             (Agent execution API)
echo   - wsgi_knowledge_api\         (Knowledge/RAG API)
echo   - wsgi_executor_service\      (Workflow executor API)
echo   - mcp_gateway\                (MCP Server Gateway)
echo   - builder_service\            (Builder Agent Service)
echo   - builder_data\               (Data Pipeline Agent)
echo   - cloud_gateway\              (Cloud Storage Gateway)
echo   - command_center_service\     (Command Center Service)
echo.
echo NOTE: ExecuteQuickJob was not included in this build.
echo       Add it if needed using the same pattern.
echo.
pause
