@echo off
setlocal enabledelayedexpansion
REM Compile all Python files to .pyd using Nuitka
REM Skips: app_*, wsgi_*, test_*, job_*, log_*, run_*, scheduler*, workflow_*, vector*, Vector*

echo ========================================
echo Compiling Python files with Nuitka
echo Excluding: app_*, wsgi_*, test_*, job_*, log_*, run_*, scheduler*, workflow_*, vector*, Vector*
echo ========================================

cd c:\src\aihub-client-ai-dev

REM Create env_dist folder
if not exist env_dist mkdir env_dist

REM Counter variables
set /a compiled=0
set /a skipped=0

REM Process each Python file
for %%f in (fast_pdf_extractor.py) do (
    set "filename=%%~nf"
    set "skip=0"
    
    REM Check if file should be skipped using string comparison
    echo !filename! | findstr /i /b "app_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "wsgi_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "test_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "job_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "log_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "run_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "scheduler" >nul && set "skip=1"
    echo !filename! | findstr /i /b "workflow_" >nul && set "skip=1"
    echo !filename! | findstr /i /b "vector" >nul && set "skip=1"
    echo !filename! | findstr /i /b "Vector" >nul && set "skip=1"
	echo !filename! | findstr /i /b "test" >nul && set "skip=1"
    
    if "!skip!"=="1" (
        echo [SKIPPED] %%f
        set /a skipped+=1
    ) else (
        echo.
        echo Compiling %%f...
        python -m nuitka --module --output-dir=env_dist --remove-output --quiet %%f
        if errorlevel 1 (
            echo   [FAILED] %%f
        ) else (
            echo   [SUCCESS] %%f
            set /a compiled+=1
        )
    )
)

echo.
echo ========================================
echo Renaming .pyd files to remove suffixes
echo ========================================

cd env_dist

REM Rename files to remove platform suffix
REM Example: GeneralAgent.cp311-win_amd64.pyd -> GeneralAgent.pyd
for %%f in (*.cp311-win_amd64.pyd) do (
    set "filename=%%~nf"
    setlocal enabledelayedexpansion
    set "newname=!filename:~0,-16!"
    ren "%%f" "!newname!.pyd"
    echo Renamed: %%f to !newname!.pyd
    endlocal
)

REM Handle Python 3.12 if you have it
for %%f in (*.cp312-win_amd64.pyd) do (
    set "filename=%%~nf"
    setlocal enabledelayedexpansion
    set "newname=!filename:~0,-16!"
    ren "%%f" "!newname!.pyd"
    echo Renamed: %%f to !newname!.pyd
    endlocal
)

cd ..

echo.
echo ========================================
echo Compilation complete!
echo Check the env_dist/ folder for .pyd files
echo ========================================
pause
