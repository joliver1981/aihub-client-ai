@echo off
REM Generate default self-signed SSL certificate for bundling with installer
REM This certificate will be used unless client provides their own

echo ============================================
echo Generating Default SSL Certificate
echo ============================================
echo.

REM Create directory if it doesn't exist
if not exist "default_certs" mkdir default_certs

REM Check if OpenSSL is available
where openssl >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: OpenSSL not found!
    echo.
    echo Please install OpenSSL:
    echo   1. Download from: https://slproweb.com/products/Win32OpenSSL.html
    echo   2. Install "Win64 OpenSSL Light"
    echo   3. Add to PATH or run from OpenSSL bin directory
    echo.
    pause
    exit /b 1
)

REM Set OpenSSL config path (try common locations)
set OPENSSL_CONF=
if exist "C:\Program Files\OpenSSL-Win64\bin\openssl.cfg" (
    set OPENSSL_CONF=C:\Program Files\OpenSSL-Win64\bin\openssl.cfg
)
if exist "C:\OpenSSL-Win64\bin\openssl.cfg" (
    set OPENSSL_CONF=C:\OpenSSL-Win64\bin\openssl.cfg
)
if exist "%ProgramFiles%\OpenSSL-Win64\bin\openssl.cfg" (
    set OPENSSL_CONF=%ProgramFiles%\OpenSSL-Win64\bin\openssl.cfg
)

REM Generate self-signed certificate valid for 10 years
REM Using -subj to avoid interactive prompts (no config file needed)
echo Generating certificate (valid for 10 years)...
echo.

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 ^
    -keyout default_certs\key.pem ^
    -out default_certs\cert.pem ^
    -subj "/C=US/ST=State/L=City/O=Organization/OU=IT/CN=AI-Application" ^
    2>nul

if %ERRORLEVEL% EQU 0 (
    echo ============================================
    echo SUCCESS: Certificate generated!
    echo ============================================
    echo.
    echo Files created:
    echo   default_certs\cert.pem
    echo   default_certs\key.pem
    echo.
    echo Certificate details:
    echo   Common Name: AI-Application
    echo   Valid for: 10 years
    echo   Key size: 2048-bit RSA
    echo.
    echo This certificate will be bundled with your installer.
    echo Users will see a browser warning on first access.
    echo.
    echo To use this certificate:
    echo   1. Set USE_SSL=true in config.ini
    echo   2. Start the application
    echo   3. Users click through browser warning
    echo.
) else (
    echo ERROR: Failed to generate certificate
    echo.
    echo Trying alternative method...
    echo.
    
    REM Try without explicit config
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 ^
        -keyout default_certs\key.pem ^
        -out default_certs\cert.pem ^
        -subj "/CN=AI-Application"
    
    if %ERRORLEVEL% EQU 0 (
        echo SUCCESS: Certificate generated using simplified method!
        echo Files created:
        echo   default_certs\cert.pem
        echo   default_certs\key.pem
    ) else (
        echo FAILED: Could not generate certificate
        echo.
        echo Please try manual generation:
        echo   openssl req -x509 -nodes -days 3650 -newkey rsa:2048 ^
        echo     -keyout default_certs\key.pem ^
        echo     -out default_certs\cert.pem
        echo.
    )
)

pause
