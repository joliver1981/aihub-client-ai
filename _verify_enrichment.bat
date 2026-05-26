@echo off
REM Verification harness for the Phase A enrichment foundation work
REM (WS1-WS4). Run from the repo root: `_verify_enrichment.bat`
REM
REM Each test module is self-contained (no DB, no LLM, no network) — they
REM mock httpx + the LLM and exercise pure-Python logic.

setlocal

echo.
echo === [WS2] tests/unit/test_web_intelligence.py ============================
python -m pytest tests\unit\test_web_intelligence.py -v
if errorlevel 1 goto :fail

echo.
echo === [WS3] tests/unit/test_enrichment.py ==================================
python -m pytest tests\unit\test_enrichment.py -v
if errorlevel 1 goto :fail

echo.
echo === [WS4] tests/unit/test_provenance.py ==================================
python -m pytest tests\unit\test_provenance.py -v
if errorlevel 1 goto :fail

echo.
echo === [WS4 UI harness] tests/static/test_provenance_badge.html =============
echo Open this file in a browser to visually verify badge rendering:
echo   tests\static\test_provenance_badge.html

echo.
echo All Phase A verification tests passed.
exit /b 0

:fail
echo.
echo One or more verification suites FAILED. Inspect output above.
exit /b 1
