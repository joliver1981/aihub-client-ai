@echo off
:: Build ONLY: Job scheduler service
:: Output:     dist/job_scheduler_service/
::
:: Usage:  build_jss.bat         (fast - reuses PyInstaller cached analysis)
::         build_jss.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" jss %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - jss
    pause
    exit /b 1
)
pause
