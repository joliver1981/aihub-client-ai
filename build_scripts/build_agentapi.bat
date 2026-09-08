@echo off
:: Build ONLY: Agent execution API
:: Output:     dist/wsgi_agent_api/
::
:: Usage:  build_agentapi.bat         (fast - reuses PyInstaller cached analysis)
::         build_agentapi.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" agentapi %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - agentapi
    pause
    exit /b 1
)
pause
