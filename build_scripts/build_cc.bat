@echo off
:: Build ONLY: Command Center service
:: Output:     dist/command_center_service/
::
:: Usage:  build_cc.bat         (fast - reuses PyInstaller cached analysis)
::         build_cc.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" cc %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - cc
    pause
    exit /b 1
)
pause
