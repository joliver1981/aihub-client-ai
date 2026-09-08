@echo off
:: Build ONLY: MCP server gateway
:: Output:     dist/mcp_gateway/
::
:: Usage:  build_mcp.bat         (fast - reuses PyInstaller cached analysis)
::         build_mcp.bat FULL    (cold rebuild, --clean)
::
:: Standalone: does not invoke Build_AIHub_Executables_OneDir_Dev_v4.bat.
:: Leaves every other dist tree untouched - check the freshness report.
call "%~dp0_build_one.bat" mcp %*
if errorlevel 1 (
    echo.
    echo BUILD FAILED - mcp
    pause
    exit /b 1
)
pause
