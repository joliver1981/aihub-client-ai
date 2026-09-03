# =============================================================================
# build_agent_service.ps1 - stage The Agent (Strategy B) for the installer
# =============================================================================
# The Agent runs main.py from SOURCE under its own isolated env (aihub-agent),
# exactly like Browser Use. The v5 installer copies two staged trees:
#   dist\agent_service\*  - the service source (this script stages it)
#   dist\agent_env\*      - a relocatable copy of the aihub-agent conda env
#
# Run from the repo root in a shell where `conda` is available:
#   powershell -ExecutionPolicy Bypass -File scripts\build_agent_service.ps1
#
# Idempotent: clears and rebuilds both dist trees.
# -----------------------------------------------------------------------------

param(
    [string]$Repo   = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvName = "aihub-agent",
    [switch]$SkipEnv          # stage source only (fast; env unchanged)
)

$ErrorActionPreference = "Stop"
$dist = Join-Path $Repo "dist"
$svcSrc = Join-Path $Repo "agent_service"
$svcDst = Join-Path $dist "agent_service"
$envDst = Join-Path $dist "agent_env"

Write-Host "=== Staging agent_service source -> $svcDst ===" -ForegroundColor Cyan
if (Test-Path $svcDst) { Remove-Item $svcDst -Recurse -Force }
New-Item -ItemType Directory -Force -Path $svcDst | Out-Null

# Copy source, EXCLUDING dev-only artifacts (matches the Browser Use staging):
#  - __pycache__ / *.pyc     (rebuilt on the target)
#  - data\*.db*              (per-install runtime state: mywork.db, views, email)
#  - *.bat                   (dev launcher; NSSM runs the service on the target)
$excludeDirs  = @("__pycache__")
robocopy $svcSrc $svcDst /E /XD @excludeDirs /XF "*.pyc" "*.db" "*.db-wal" "*.db-shm" "start_agent_service_dev.bat" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging agent_service ($LASTEXITCODE)" }
# Never ship a populated runtime data dir; the service recreates data\agent\ on boot.
$dataDir = Join-Path $svcDst "data"
if (Test-Path $dataDir) { Remove-Item $dataDir -Recurse -Force }
Write-Host "  staged $(Get-ChildItem $svcDst -Recurse -File | Measure-Object | Select-Object -ExpandProperty Count) files" -ForegroundColor Green

# Private copy of the command_center.tools modules The Agent imports (portal registry /
# fetch / workflows / run). Service-local ON PURPOSE: a partial copy shipped loose to
# {app} shadowed the Command Center exe's bundled package and killed every CC chat
# ("No module named 'command_center.orchestration'", 2026-09-01). The helper also fails
# the build if a new command_center.* import escapes the shipped set.
& (Join-Path $PSScriptRoot "stage_cc_tools_subset.ps1") -Dest $svcDst -Repo $Repo

# Private copy of the shared code_exec package (code-interpreter backend) for the same
# reason: it lives at the repo root and inside the frozen exes, and nothing shipped it for
# the source-run Agent, so run_python / export_data / manipulate_pdf died on every install
# with "No module named 'code_exec'" (pack-20 per-tool smoke, 2026-09-03). Closure-checked.
& (Join-Path $PSScriptRoot "stage_code_exec.ps1") -Dest $svcDst -Repo $Repo

if ($SkipEnv) {
    Write-Host "=== -SkipEnv: leaving $envDst as-is ===" -ForegroundColor Yellow
    return
}

Write-Host "=== Staging aihub-agent env -> $envDst (conda-pack) ===" -ForegroundColor Cyan
# conda-pack produces a RELOCATABLE copy (the env's absolute paths are rewritten
# so it runs from {app}\agent_env on the target). Install once: `pip install conda-pack`.
$envPrefix = (& conda env list) `
    | Where-Object { $_ -match "[\\/]$([regex]::Escape($EnvName))\s*$" } `
    | ForEach-Object { ($_ -split "\s+")[-1] } | Select-Object -First 1
if (-not $envPrefix) { throw "conda env '$EnvName' not found" }
Write-Host "  source env: $envPrefix"

if (Test-Path $envDst) { Remove-Item $envDst -Recurse -Force }
New-Item -ItemType Directory -Force -Path $envDst | Out-Null
$tmpTar = Join-Path $env:TEMP "agent_env.tar"
if (Test-Path $tmpTar) { Remove-Item $tmpTar -Force }
& conda-pack -p $envPrefix -o $tmpTar --format tar
if ($LASTEXITCODE -ne 0) { throw "conda-pack failed ($LASTEXITCODE)" }
& tar -xf $tmpTar -C $envDst
Remove-Item $tmpTar -Force
if (-not (Test-Path (Join-Path $envDst "python.exe"))) {
    throw "conda-pack output has no python.exe at the env root - check the env"
}
Write-Host "  staged agent_env (python.exe present)" -ForegroundColor Green
Write-Host "`nDONE. The v5 installer will copy dist\agent_service and dist\agent_env." -ForegroundColor Cyan
# (ASCII-only strings above: the installer build box reads this file as cp1252.)
