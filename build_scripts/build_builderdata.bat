@echo off
:: Build ONLY: Data pipeline agent
:: Output:     dist/builder_data/
::
:: Usage:  build_builderdata.bat         (fast - reuses PyInstaller cached analysis)
::         build_builderdata.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" builderdata %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - builderdata
    pause
    exit /b 1
)
pause
