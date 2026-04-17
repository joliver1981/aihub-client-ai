@echo off
setlocal

echo AI Hub Services Manager (AI-DEV) - STOP Script
echo ==============================================
echo.

echo Looking for AIHub-DEV windows...
for /f %%P in ('powershell -NoProfile -Command "Get-Process | Where-Object { $_.MainWindowTitle -like 'AIHub-DEV*' } | Select-Object -ExpandProperty Id"') do (
    echo Stopping window PID %%P ...
    taskkill /PID %%P /T /F
)

echo Looking for AIHub-DEV windows run as Administrator...
for /f %%P in ('powershell -NoProfile -Command "Get-Process | Where-Object { $_.MainWindowTitle -like 'Administrator:*AIHub-DEV*' } | Select-Object -ExpandProperty Id"') do (
    echo Stopping admin window PID %%P ...
    taskkill /PID %%P /T /F
)

echo.
echo Cleaning up any leftover python service processes...
for /f %%P in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -match 'wsgi.py' -or $_.CommandLine -match 'wsgi_doc_api.py' -or $_.CommandLine -match 'app_doc_job_q.py' -or $_.CommandLine -match 'app_jss_main.py' -or $_.CommandLine -match 'wsgi_vector_api.py' -or $_.CommandLine -match 'wsgi_agent_api.py' -or $_.CommandLine -match 'wsgi_knowledge_api.py' -or $_.CommandLine -match 'wsgi_executor_service.py' -or $_.CommandLine -match 'app_mcp_gateway.py' -or $_.CommandLine -match 'app_cloud_gateway.py' -or $_.CommandLine -match 'builder_service' -or $_.CommandLine -match 'builder_data' -or $_.CommandLine -match 'python main.py') } | Select-Object -ExpandProperty ProcessId"') do (
    echo Killing leftover python PID %%P ...
    taskkill /PID %%P /T /F
)

echo.
echo Cleaning up any leftover python service processes that were run as admin...
for /f %%P in ('powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'Administrator:*' -and $_.MainWindowTitle -match 'wsgi|app_doc_job_q|app_jss_main|app_mcp_gateway|app_cloud_gateway|builder_service|builder_data|python main.py' } | Select-Object -ExpandProperty Id"') do (
    echo Killing leftover admin python PID %%P ...
    taskkill /PID %%P /T /F
)


echo.
echo Done.
echo.
endlocal
