@echo off
:: Build ONLY: Builder agent service
:: Output:     dist/builder_service/
::
:: Usage:  build_builder.bat         (fast - reuses PyInstaller cached analysis)
::         build_builder.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" builder %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - builder
    pause
    exit /b 1
)
pause
