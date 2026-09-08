@echo off
:: Build ONLY: Knowledge / RAG API
:: Output:     dist/wsgi_knowledge_api/
::
:: Usage:  build_knowledge.bat         (fast - reuses PyInstaller cached analysis)
::         build_knowledge.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" knowledge %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - knowledge
    pause
    exit /b 1
)
pause
