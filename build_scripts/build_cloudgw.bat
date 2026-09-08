@echo off
:: Build ONLY: Cloud storage gateway
:: Output:     dist/cloud_gateway/
::
:: Usage:  build_cloudgw.bat         (fast - reuses PyInstaller cached analysis)
::         build_cloudgw.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" cloudgw %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - cloudgw
    pause
    exit /b 1
)
pause
