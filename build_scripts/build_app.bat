@echo off
:: Build ONLY: Main web application
:: Output:     dist/app/
::
:: Usage:  build_app.bat         (fast - reuses PyInstaller cached analysis)
::         build_app.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" app %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - app
    pause
    exit /b 1
)
pause
