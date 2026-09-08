@echo off
:: Build ONLY: Document job queue
:: Output:     dist/document_job_processor/
::
:: Usage:  build_docjob.bat         (fast - reuses PyInstaller cached analysis)
::         build_docjob.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" docjob %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - docjob
    pause
    exit /b 1
)
pause
