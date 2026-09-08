@echo off
:: Build ONLY: Document processing API
:: Output:     dist/document_api_server/
::
:: Usage:  build_docapi.bat         (fast - reuses PyInstaller cached analysis)
::         build_docapi.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" docapi %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - docapi
    pause
    exit /b 1
)
pause
