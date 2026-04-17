@echo off
echo ============================================================
echo  AI Hub OneDir Build Process (AI-DEV) v2
echo  Creates folder-based executables (AV-friendly)
echo  Source: C:\src\aihub-client-ai-dev
echo  Services: 12 total
echo ============================================================
echo.

:: Set the path to the Anaconda/Miniconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"

:: Set the project folder path - AI DEV version
SET "PROJECT_PATH=C:\src\aihub-client-ai-dev"

:: Initialize Conda
CALL "%CONDA_PATH%\Scripts\activate.bat"

echo.
echo [1/12] Building app_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL conda activate aihub2.1
cd /d %PROJECT_PATH%
pyinstaller app_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [1/12] Complete - Output: dist\app\

echo.
echo [2/12] Building wsgi_doc_api_onedir.spec with aihubant environment...
echo --------------------------------------------------------
CALL conda activate aihubant
cd /d %PROJECT_PATH%
pyinstaller wsgi_doc_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_doc_api_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [2/12] Complete - Output: dist\document_api_server\

echo.
echo [3/12] Building app_doc_job_q_onedir.spec with aihubant environment...
echo --------------------------------------------------------
CALL conda activate aihubant
cd /d %PROJECT_PATH%
pyinstaller app_doc_job_q_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_doc_job_q_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [3/12] Complete - Output: dist\document_job_processor\

echo.
echo [4/12] Building app_jss_main_onedir.spec with jss environment...
echo --------------------------------------------------------
CALL conda activate jss
cd /d %PROJECT_PATH%
pyinstaller app_jss_main_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: app_jss_main_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [4/12] Complete - Output: dist\job_scheduler_service\

echo.
echo [5/12] Building wsgi_vector_api_onedir.spec with aihubvector2 environment...
echo --------------------------------------------------------
CALL conda activate aihubvector2
cd /d %PROJECT_PATH%
pyinstaller wsgi_vector_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_vector_api_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [5/12] Complete - Output: dist\wsgi_vector_api\

echo.
echo [6/12] Building wsgi_agent_api_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL conda activate aihub2.1
cd /d %PROJECT_PATH%
pyinstaller wsgi_agent_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_agent_api_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [6/12] Complete - Output: dist\wsgi_agent_api\

echo.
echo [7/12] Building wsgi_knowledge_api_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL conda activate aihub2.1
cd /d %PROJECT_PATH%
pyinstaller wsgi_knowledge_api_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_knowledge_api_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [7/12] Complete - Output: dist\wsgi_knowledge_api\

echo.
echo [8/12] Building wsgi_executor_service_onedir.spec with aihub2.1 environment...
echo --------------------------------------------------------
CALL conda activate aihub2.1
cd /d %PROJECT_PATH%
pyinstaller wsgi_executor_service_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: wsgi_executor_service_onedir.spec build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [8/12] Complete - Output: dist\wsgi_executor_service\

echo.
echo ============================================================
echo  SERVICES - MCP Gateway, Builder Service, Builder Data,
echo             Cloud Storage Gateway
echo ============================================================

echo.
echo [9/12] Building MCP Gateway with aihubmcp environment...
echo --------------------------------------------------------
CALL conda activate aihubmcp
cd /d %PROJECT_PATH%
pyinstaller builder_mcp/gateway/app_mcp_gateway_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: MCP Gateway build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [9/12] Complete - Output: dist\mcp_gateway\

echo.
echo [10/12] Building Builder Service with aihubbuilder environment...
echo --------------------------------------------------------
CALL conda activate aihubbuilder
cd /d %PROJECT_PATH%
pyinstaller builder_service/builder_service_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Builder Service build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [10/12] Complete - Output: dist\builder_service\

echo.
echo [11/12] Building Builder Data Service with aihubbuilder environment...
echo --------------------------------------------------------
CALL conda activate aihubbuilder
cd /d %PROJECT_PATH%
pyinstaller builder_data/builder_data_onedir.spec --clean --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Builder Data Service build failed!
    pause
    exit /b 1
)
CALL conda deactivate
echo [11/12] Complete - Output: dist\builder_data\

echo.
echo [12/12] Building Cloud Storage Gateway with aihubcloudgateway environment...
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
echo [12/12] Complete - Output: dist\cloud_gateway\

echo.
echo ============================================================
echo  BUILD COMPLETE - OneDir Structure (AI-DEV) v2
echo ============================================================
echo.
echo Output folders created in %PROJECT_PATH%\dist\:
echo   - app\                     (Main web application)
echo   - document_api_server\     (Document processing API)
echo   - document_job_processor\  (Document job queue)
echo   - job_scheduler_service\   (Job scheduler)
echo   - wsgi_vector_api\         (Vector search API)
echo   - wsgi_agent_api\          (Agent execution API)
echo   - wsgi_knowledge_api\      (Knowledge/RAG API)
echo   - wsgi_executor_service\   (Workflow executor API)
echo   - mcp_gateway\             (MCP Server Gateway)
echo   - builder_service\         (Builder Agent Service)
echo   - builder_data\            (Data Pipeline Agent)
echo   - cloud_gateway\           (Cloud Storage Gateway)
echo.
echo NOTE: ExecuteQuickJob was not included in this build.
echo       Add it if needed using the same pattern.
echo.
pause
