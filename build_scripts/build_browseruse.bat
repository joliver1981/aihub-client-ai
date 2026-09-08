@echo off
:: Build ONLY: Browser Use / portal RPA
:: Output:     dist/browser_use_service/
::
:: Usage:  build_browseruse.bat         (fast - reuses PyInstaller cached analysis)
::         build_browseruse.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" browseruse %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - browseruse
    pause
    exit /b 1
)
pause
