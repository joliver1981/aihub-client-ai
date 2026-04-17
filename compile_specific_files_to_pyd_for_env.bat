@echo off
REM Check for administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ========================================
    echo Administrator privileges required!
    echo Requesting elevation...
    echo ========================================
    REM Re-launch with admin rights, preserving the current directory
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%CD%\" && \"%~f0\"' -Verb RunAs"
    exit /b
)

setlocal enabledelayedexpansion

REM ========================================
REM CONFIGURATION - Edit these values
REM ========================================

REM Python environment to use
set "PYTHON_ENV=C:\Users\james\miniconda3\envs\aihub2.1"
set "PYTHON_CMD=%PYTHON_ENV%\python.exe"

REM Source folder containing .py files to compile
set "SOURCE_FOLDER=C:\src\aihub-client-ai-dev"

REM Destination folders for compiled .pyd files
set "DEST_FOLDER_1=C:\src\aihub-client-ai-dev\dist\python-bundle\Lib\site-packages"
set "DEST_FOLDER_2=C:\src\aihub-client-ai-dev\agent_environments\python-bundle"

REM ========================================
REM FILES TO COMPILE - Add/remove as needed
REM Just the base name without .py extension
REM ========================================
set "FILES_TO_COMPILE=agent_excel_tools RichContentManager"

REM ========================================
REM END CONFIGURATION
REM ========================================

echo ========================================
echo Compiling Python files with Nuitka
echo Using specified Conda environment
echo Running with Administrator privileges
echo ========================================

REM Store the current directory
set "ORIGINAL_DIR=%CD%"
echo Current working directory: %ORIGINAL_DIR%
echo Python environment: %PYTHON_ENV%
echo Source folder for .py files: %SOURCE_FOLDER%
echo.

REM Check if Python exists in the environment
if not exist "%PYTHON_CMD%" (
    echo ERROR: Python not found at:
    echo %PYTHON_CMD%
    echo.
    echo Please verify the Conda environment path is correct.
    pause
    exit /b 1
)

REM Verify Python
echo Checking Python:
echo Python executable: %PYTHON_CMD%
"%PYTHON_CMD%" --version
echo.

REM Check if source folder exists
if not exist "%SOURCE_FOLDER%" (
    echo ERROR: Source folder not found:
    echo %SOURCE_FOLDER%
    pause
    exit /b 1
)

echo.
echo Files to compile:
for %%f in (%FILES_TO_COMPILE%) do (
    if exist "%SOURCE_FOLDER%\%%f.py" (
        echo   [FOUND] %%f.py
    ) else (
        echo   [MISSING] %%f.py
    )
)
echo.

REM Ask for confirmation
set /p confirm="Continue with compilation? (y/n): "
if /i not "%confirm%"=="y" (
    echo Aborted by user
    pause
    exit /b 1
)

echo.
echo Proceeding with compilation...
echo.

REM Create dist_env folder in the source directory
if not exist "%SOURCE_FOLDER%\dist_env" mkdir "%SOURCE_FOLDER%\dist_env"

REM Counter variables
set /a compiled=0
set /a skipped=0
set /a failed=0
set /a total=0

REM Process each file in the list
for %%f in (%FILES_TO_COMPILE%) do (
    set /a total+=1
    
    if exist "%SOURCE_FOLDER%\%%f.py" (
        echo.
        echo Compiling %%f.py...
        "%PYTHON_CMD%" -m nuitka --module --output-dir="%SOURCE_FOLDER%\dist_env" --remove-output --quiet "%SOURCE_FOLDER%\%%f.py"
        if errorlevel 1 (
            echo   [FAILED] %%f.py
            set /a failed+=1
        ) else (
            echo   [SUCCESS] %%f.py
            set /a compiled+=1
        )
    ) else (
        echo [NOT FOUND] %%f.py in %SOURCE_FOLDER%
        set /a skipped+=1
    )
)

echo.
echo ========================================
echo Renaming .pyd files to remove suffixes
echo ========================================

cd "%SOURCE_FOLDER%\dist_env"

set /a renamed=0
set /a deleted_existing=0
REM Rename files to remove platform suffix
for %%f in (*.cp*-win_amd64.pyd) do (
    REM Extract base name without the platform suffix
    set "fullname=%%~nf"
    REM Remove everything after .cp
    for /f "tokens=1 delims=." %%a in ("!fullname!") do (
        set "basename=%%a"
    )
    
    REM Delete existing .pyd file with the target name if it exists
    if exist "!basename!.pyd" (
        echo Deleting existing: !basename!.pyd
        del "!basename!.pyd"
        set /a deleted_existing+=1
    )
    
    ren "%%f" "!basename!.pyd"
    echo Renamed: %%f to !basename!.pyd
    set /a renamed+=1
)

echo.
echo ========================================
echo Deleting .pyi files
echo ========================================

set /a deleted_pyi=0
for %%f in (*.pyi) do (
    echo Deleting: %%f
    del "%%f"
    set /a deleted_pyi+=1
)

if !deleted_pyi!==0 (
    echo No .pyi files found to delete
) else (
    echo Deleted !deleted_pyi! .pyi files
)

cd "%ORIGINAL_DIR%"

echo.
echo ========================================
echo Copying .pyd files to destination folders
echo ========================================

REM Create destination folders if they don't exist
if not exist "%DEST_FOLDER_1%" (
    echo Creating destination folder: %DEST_FOLDER_1%
    mkdir "%DEST_FOLDER_1%" 2>nul
)

if not exist "%DEST_FOLDER_2%" (
    echo Creating destination folder: %DEST_FOLDER_2%
    mkdir "%DEST_FOLDER_2%" 2>nul
)

set /a copied_1=0
set /a copied_2=0
set /a failed_1=0
set /a failed_2=0

echo.
echo Copying to: %DEST_FOLDER_1%
for %%f in ("%SOURCE_FOLDER%\dist_env\*.pyd") do (
    echo   Copying: %%~nxf
    copy /Y "%%f" "%DEST_FOLDER_1%\" >nul 2>&1
    if errorlevel 1 (
        echo   [FAILED] %%~nxf - Access Denied or file in use
        set /a failed_1+=1
    ) else (
        set /a copied_1+=1
    )
)

echo.
echo Copying to: %DEST_FOLDER_2%
for %%f in ("%SOURCE_FOLDER%\dist_env\*.pyd") do (
    echo   Copying: %%~nxf
    copy /Y "%%f" "%DEST_FOLDER_2%\" >nul 2>&1
    if errorlevel 1 (
        echo   [FAILED] %%~nxf - Access Denied or file in use
        set /a failed_2+=1
    ) else (
        set /a copied_2+=1
    )
)

echo.
echo ========================================
echo Compilation complete!
echo Files specified: !total!
echo Files compiled: !compiled!
echo Files not found/skipped: !skipped!
echo Files failed: !failed!
echo .pyi files deleted: !deleted_pyi!
echo Files copied to folder 1: !copied_1! (failed: !failed_1!)
echo Files copied to folder 2: !copied_2! (failed: !failed_2!)
echo ========================================

pause
