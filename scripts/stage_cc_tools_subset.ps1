# =============================================================================
# stage_cc_tools_subset.ps1 - give a SOURCE-RUN service (agent_service, browser_use_service)
# its own private copy of the command_center.tools modules it imports.
# =============================================================================
# Why service-local and NOT loose at {app}:
#   Installer v4/v5 used to ship a PARTIAL `command_center` package loose to {app}
#   (__init__.py + tools\portal_workflows.py) for browser_use_service. config.py puts {app}
#   at sys.path[0] in every service, and command_center_service.exe is built with a
#   PyInstaller whose frozen finder is a path hook (>= 6.10), so that stray folder shadowed
#   the exe's bundled package and every Command Center chat died with
#   "No module named 'command_center.orchestration'" (client bug, 2026-09-01).
#   A copy INSIDE the service folder is visible only to that service (Python puts the
#   directory of main.py first on sys.path) and never to a frozen exe.
#
# Usage (idempotent - replaces <Dest>\command_center):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stage_cc_tools_subset.ps1 `
#       -Dest C:\src\aihub-client-ai-dev\dist\agent_service
# Called by scripts\build_agent_service.ps1 (The Agent) and by the browser-use step of
# Build_AIHub_Executables_OneDir_Dev_v3.bat. The installer ships dist\<service>\* recursively,
# so the copy rides along with no .iss change.
#
# $Files is the CLOSED set: every `command_center.*` import inside the shipped files must
# resolve to another shipped file (or be listed in $Optional because the code guards it with
# try/except and degrades). The script FAILS THE BUILD otherwise, so a future edit to one of
# these modules cannot silently break clients again. Repo-root modules they import lazily:
#   local_secrets  - shipped loose to {app} by the installer (portal_registry.save_portal)
#   CommonUtils    - NOT shipped (drags in config.py); portal_fetch.browser_use_base_url()
#                    falls back to the same env rules when it is absent.
# ASCII-only on purpose: the installer build box reads this file as cp1252.
# -----------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true)][string]$Dest,   # staged service folder, e.g. dist\agent_service
    [string]$Repo = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = "Stop"

# Repo-relative. Package skeleton first, then the modules.
$Files = @(
    "command_center\__init__.py",
    "command_center\tools\__init__.py",
    "command_center\tools\portal_workflows.py",
    "command_center\tools\portal_registry.py",
    "command_center\tools\portal_fetch.py",
    "command_center\tools\portal_workflow_run.py"
)
# Imports that are allowed to escape the set because the importing code wraps them in
# try/except and degrades (verified by tests_v2\unit\test_stage_cc_tools_subset.py):
#   portal_fetch._register_artifacts: ArtifactType -> None, downloads are still returned.
$Optional = @(
    "command_center.artifacts.artifact_models"
)

if (-not (Test-Path $Dest -PathType Container)) {
    throw "stage_cc_tools_subset: Dest '$Dest' is not a directory (stage the service source first)"
}
$pkgDst = Join-Path $Dest "command_center"
if (Test-Path $pkgDst) { Remove-Item $pkgDst -Recurse -Force }

foreach ($rel in $Files) {
    $src = Join-Path $Repo $rel
    if (-not (Test-Path $src -PathType Leaf)) { throw "stage_cc_tools_subset: missing source file $src" }
    $dst = Join-Path $Dest $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item $src $dst -Force
}

# ---- closure check ----------------------------------------------------------------------
$shippedPkgs = @{}   # dotted package names (from __init__.py entries)
$shippedMods = @{}   # dotted module names
foreach ($rel in $Files) {
    $dotted = (($rel -replace '\\', '.') -replace '\.py$', '')
    if ($dotted.EndsWith('.__init__')) { $shippedPkgs[$dotted.Substring(0, $dotted.Length - 9)] = $true }
    else { $shippedMods[$dotted] = $true }
}
function Test-Shipped([string]$m) {
    return ($shippedMods.ContainsKey($m) -or $shippedPkgs.ContainsKey($m) -or ($Optional -contains $m))
}

$escapes = @()
foreach ($rel in $Files) {
    $lineNo = 0
    foreach ($line in (Get-Content (Join-Path $Repo $rel) -Encoding UTF8)) {
        $lineNo++
        $t = $line.Trim()
        if ($t.StartsWith("#")) { continue }
        if ($t -match '^from\s+(command_center(?:\.[A-Za-z0-9_]+)*)\s+import\s+(.+)$') {
            $base = $Matches[1]
            $names = $Matches[2] -replace '[()\\]', ''
            # `from <shipped module> import symbol` needs nothing more.
            if ($shippedMods.ContainsKey($base) -or ($Optional -contains $base)) { continue }
            # `from <package> import name` imports a SUBMODULE (the package __init__ files are
            # empty), so each name must itself be shipped.
            foreach ($n in ($names -split ',')) {
                $name = (($n.Trim()) -split '\s+as\s+')[0].Trim()
                if ($name -and -not (Test-Shipped "$base.$name")) { $escapes += ("{0}:{1}  {2}" -f $rel, $lineNo, $t) }
            }
        }
        elseif ($t -match '^import\s+(command_center(?:\.[A-Za-z0-9_]+)*)') {
            if (-not (Test-Shipped $Matches[1])) { $escapes += ("{0}:{1}  {2}" -f $rel, $lineNo, $t) }
        }
    }
}
if ($escapes.Count -gt 0) {
    Write-Host "stage_cc_tools_subset: command_center imports that ESCAPE the shipped set:" -ForegroundColor Red
    $escapes | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    throw "stage_cc_tools_subset: closure check failed - add the module to `$Files (or `$Optional if the import is guarded) in scripts\stage_cc_tools_subset.ps1"
}
Write-Host ("  staged {0} command_center files -> {1} (closure OK)" -f $Files.Count, $pkgDst) -ForegroundColor Green
