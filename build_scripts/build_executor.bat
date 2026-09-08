@echo off
:: Build ONLY: Workflow executor API
:: Output:     dist/wsgi_executor_service/
::
:: Usage:  build_executor.bat         (fast - reuses PyInstaller cached analysis)
::         build_executor.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" executor %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - executor
    pause
    exit /b 1
)
pause
