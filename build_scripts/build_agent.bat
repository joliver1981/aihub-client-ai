@echo off
:: Build ONLY: The Agent
:: Output:     dist/agent_service/
::
:: Usage:  build_agent.bat         (fast - reuses PyInstaller cached analysis)
::         build_agent.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" agent %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - agent
    pause
    exit /b 1
)
pause
