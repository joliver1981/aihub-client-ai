@echo off
:: Build ONLY: Vector search API
:: Output:     dist/wsgi_vector_api/
::
:: Usage:  build_vector.bat         (fast - reuses PyInstaller cached analysis)
::         build_vector.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" vector %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - vector
    pause
    exit /b 1
)
pause
