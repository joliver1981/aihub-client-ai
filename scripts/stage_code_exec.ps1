# =============================================================================
# stage_code_exec.ps1 - give a SOURCE-RUN service (agent_service) its own private copy
# of the shared code_exec package (the code-interpreter execution backend).
# =============================================================================
# Why: agent_service/code_tools.py does `from code_exec import ...`. code_exec/ lives at
# the repo root, which is on sys.path in the dev tree, and it is bundled INSIDE the frozen
# exes (app.exe / command_center_service.exe) for their surfaces. The Agent runs from
# SOURCE on a client ({app}\agent_service) and nothing shipped the package for it, so on
# every install run_python / export_data / manipulate_pdf died with
# "No module named 'code_exec'" (found by the pack-20 per-tool smoke, 2026-09-03).
#
# Service-local, NOT loose at {app}: a loose copy at {app} would sit at sys.path[0] for
# every service (config.py inserts {app} there) and shadow the frozen exes' bundled
# package under PyInstaller's path-based finder - the exact mechanism behind the
# command_center.orchestration client bug of 2026-09-01. A copy inside the service folder
# is visible only to that service.
#
# Usage (idempotent - replaces <Dest>\code_exec):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stage_code_exec.ps1 `
#       -Dest C:\src\aihub-client-ai-dev\dist\agent_service
# Called by scripts\build_agent_service.ps1. The installer ships dist\agent_service\*
# recursively, so the copy rides along with no .iss change.
#
# $Files is the CLOSED set: every `code_exec.*` import inside the shipped files must resolve
# to another shipped file. Repo-root modules code_exec imports lazily, inside try/except,
# and degrades without (verified by tests_v2\unit\test_stage_code_exec.py):
#   CommonUtils  - NOT shipped (drags in config.py); sdkwire falls back to APP_ROOT / env rules
#   shared_auth  - shipped loose to {app} by the installer
# ASCII-only on purpose: the installer build box reads this file as cp1252.
# -----------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true)][string]$Dest,   # staged service folder, e.g. dist\agent_service
    [string]$Repo = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = "Stop"

$Files = @(
    "code_exec\__init__.py",
    "code_exec\doctrine.py",
    "code_exec\envbuild.py",
    "code_exec\executor.py",
    "code_exec\interpreter.py",
    "code_exec\jobguard.py",
    "code_exec\preamble.py",
    "code_exec\sdkwire.py",
    "code_exec\workbooks.py"
)

if (-not (Test-Path $Dest -PathType Container)) {
    throw "stage_code_exec: Dest '$Dest' is not a directory (stage the service source first)"
}
# Every .py in the source package must be in the declared set - a new module added to
# code_exec/ without updating $Files would ship a broken package.
$srcPkg = Join-Path $Repo "code_exec"
$actual = Get-ChildItem $srcPkg -Filter *.py -File | ForEach-Object { "code_exec\" + $_.Name }
$undeclared = @($actual | Where-Object { $Files -notcontains $_ })
if ($undeclared.Count -gt 0) {
    throw ("stage_code_exec: code_exec/ has modules not in `$Files: {0} - add them" -f ($undeclared -join ", "))
}

$pkgDst = Join-Path $Dest "code_exec"
if (Test-Path $pkgDst) { Remove-Item $pkgDst -Recurse -Force }
foreach ($rel in $Files) {
    $src = Join-Path $Repo $rel
    if (-not (Test-Path $src -PathType Leaf)) { throw "stage_code_exec: missing source file $src" }
    $dst = Join-Path $Dest $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item $src $dst -Force
}

# ---- closure check: every code_exec.* import resolves inside the shipped set ------------
$shipped = @{}
foreach ($rel in $Files) {
    $dotted = (($rel -replace '\\', '.') -replace '\.py$', '')
    if ($dotted.EndsWith('.__init__')) { $shipped[$dotted.Substring(0, $dotted.Length - 9)] = $true }
    else { $shipped[$dotted] = $true }
}
$escapes = @()
foreach ($rel in $Files) {
    $lineNo = 0
    foreach ($line in (Get-Content (Join-Path $Repo $rel) -Encoding UTF8)) {
        $lineNo++
        $t = $line.Trim()
        if ($t.StartsWith("#")) { continue }
        if ($t -match '^from\s+(code_exec(?:\.[A-Za-z0-9_]+)*)\s+import\s+') {
            if (-not $shipped.ContainsKey($Matches[1])) { $escapes += ("{0}:{1}  {2}" -f $rel, $lineNo, $t) }
        }
        elseif ($t -match '^import\s+(code_exec(?:\.[A-Za-z0-9_]+)*)') {
            if (-not $shipped.ContainsKey($Matches[1])) { $escapes += ("{0}:{1}  {2}" -f $rel, $lineNo, $t) }
        }
    }
}
if ($escapes.Count -gt 0) {
    Write-Host "stage_code_exec: code_exec imports that ESCAPE the shipped set:" -ForegroundColor Red
    $escapes | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    throw "stage_code_exec: closure check failed - add the module to `$Files in scripts\stage_code_exec.ps1"
}
Write-Host ("  staged {0} code_exec files -> {1} (closure OK)" -f $Files.Count, $pkgDst) -ForegroundColor Green
