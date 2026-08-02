<#
.SYNOPSIS
  Skeleton for the T2 cold rebuild (DEV_MACHINE_PROVISIONING.md §4): fresh Windows +
  seed package -> working dev machine. Run phase by phase, supervised — NOT fire-and-forget.

.DESCRIPTION
  Default is a DRY RUN that prints what each phase would do. Add -Execute to act.
  -SeedPath points at one timestamped folder produced by backup_dev_machine.ps1
  (with Core\, and ideally Data\ + Envs\ from a full backup).

.EXAMPLE
  .\bootstrap_dev_machine.ps1 -SeedPath S:\DevMachineBackups\20260802_1200 -Phase 1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SeedPath,
    [int[]]$Phase = @(0),
    [switch]$Execute
)

$Conda = "$env:USERPROFILE\miniconda3"
function Act { param([string]$What, [scriptblock]$Do)
    if ($Execute) { Write-Host "DO  : $What"; & $Do } else { Write-Host "PLAN: $What" }
}

if (-not (Test-Path "$SeedPath\Core")) { throw "No Core\ under $SeedPath - is this a seed package?" }

# ---- Phase 0: preflight -------------------------------------------------
if ($Phase -contains 0) {
    Write-Host "`n--- Phase 0: preflight ---"
    if ($env:USERNAME -ne 'james') {
        Write-Warning "Username is '$env:USERNAME', not 'james'. Build bats + saved paths assume 'james'; either recreate the user or plan a find/replace pass (see plan section 4 step 0)."
    }
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Warning 'Run elevated.' }
    if (Test-Path "$SeedPath\Core\_sensitive.7z") {
        Write-Warning 'Sensitive bundle is sealed (_sensitive.7z) - extract it to Core\_sensitive first (7z x).'
    }
    Write-Host "Seed provenance:"; Get-Content "$SeedPath\Core\provenance.json" -ErrorAction SilentlyContinue
}

# ---- Phase 1: toolchain -------------------------------------------------
if ($Phase -contains 1) {
    Write-Host "`n--- Phase 1: toolchain installs ---"
    foreach ($id in 'Git.Git','JRSoftware.InnoSetup','OpenJS.NodeJS.LTS','Microsoft.AzureCLI',
                    '7zip.7zip','Microsoft.VisualStudioCode','GitHub.cli') {
        Act "winget install $id" { winget install --id $id -e --accept-package-agreements --accept-source-agreements }
    }
    Act 'choco install upx (specs use upx=True)' { choco install upx -y }
    Write-Host 'MANUAL: Miniconda3 -> install to %USERPROFILE%\miniconda3 (exact path matters).'
    Write-Host 'MANUAL: ODBC Driver 17 for SQL Server (msodbcsql17 x64 msi) - pyodbc fails without it.'
    Write-Host 'MANUAL: OpenSSL-Win64 only if regenerating SSL certs (setup_ssl.bat).'
}

# ---- Phase 2: source trees ---------------------------------------------
if ($Phase -contains 2) {
    Write-Host "`n--- Phase 2: repos + machine-only trees into C:\src ---"
    Act 'mkdir C:\src' { New-Item -ItemType Directory -Force C:\src | Out-Null }
    # Remoted repos: prefer fresh clones; bundles are the fallback.
    foreach ($r in 'aihub-client-ai:aihub-client-ai-dev','aihub-email:aihub-email','aihub-api:aihub-api',
                   'aihub-installer:aihub-installer','aihub-marketplace:aihub-marketplace') {
        $remote, $dir = $r -split ':'
        Act "git clone github.com/joliver1981/$remote -> C:\src\$dir" {
            if (-not (Test-Path "C:\src\$dir")) { git clone "https://github.com/joliver1981/$remote.git" "C:\src\$dir" } }
    }
    Get-ChildItem "$SeedPath\Core\git_bundles\*.bundle" -ErrorAction SilentlyContinue | ForEach-Object {
        $name = $_.BaseName
        Act "unbundle $name -> C:\src\$name (only if not cloned above)" {
            if (-not (Test-Path "C:\src\$name")) { git clone $_.FullName "C:\src\$name" } }
    }
    foreach ($t in 'ai-colab','dummy-service','Inno_Build_Scripts','nssm-2.24') {
        Act "restore C:\src\$t from seed" { robocopy "$SeedPath\Core\src\$t" "C:\src\$t" /E /XJ /NFL /NDL /NP | Out-Null }
    }
    Act 'restore C:\scripts' { robocopy "$SeedPath\Core\scripts" 'C:\scripts' /E /NFL /NDL /NP | Out-Null }
}

# ---- Phase 3: machine-only repo overlay --------------------------------
if ($Phase -contains 3) {
    Write-Host "`n--- Phase 3: overlay untracked files onto the repo ---"
    foreach ($pair in @(@("$SeedPath\Core\repo",       'C:\src\aihub-client-ai-dev'),
                        @("$SeedPath\Core\_sensitive\repo", 'C:\src\aihub-client-ai-dev'),
                        @("$SeedPath\Data\repo",        'C:\src\aihub-client-ai-dev'))) {
        $src, $dst = $pair
        if (Test-Path $src) { Act "overlay $src -> $dst" { robocopy $src $dst /E /XJ /NFL /NDL /NP | Out-Null } }
    }
    if (Test-Path "$SeedPath\Core\_sensitive\aihub-email\.env") {
        Act 'restore aihub-email\.env' { Copy-Item "$SeedPath\Core\_sensitive\aihub-email\.env" 'C:\src\aihub-email\.env' -Force }
    }
}

# ---- Phase 4: conda envs (copy, never pip-rebuild) ---------------------
if ($Phase -contains 4) {
    Write-Host "`n--- Phase 4: conda envs ---"
    if (-not (Test-Path "$Conda\Scripts\conda.exe")) { Write-Warning "Miniconda not at $Conda - do Phase 1 first." }
    if (Test-Path "$SeedPath\Envs\conda") {
        Get-ChildItem "$SeedPath\Envs\conda" -Directory | ForEach-Object {
            Act "restore env $($_.Name) -> $Conda\envs\$($_.Name)" {
                robocopy $_.FullName "$Conda\envs\$($_.Name)" /E /XJ /NFL /NDL /NP | Out-Null }
        }
    } else { Write-Warning 'Seed has no Envs tier - restore envs from a full backup or the disk clone. pip freeze manifests in Core\conda_manifests are DOCUMENTATION, not an install path (locally patched packages - see plan Appendix C).' }
    foreach ($pair in @(@("$SeedPath\Envs\dist\python-bundle", 'C:\src\aihub-client-ai-dev\dist\python-bundle'),
                        @("$SeedPath\Envs\repo\agent_environments\python-bundle", 'C:\src\aihub-client-ai-dev\agent_environments\python-bundle'),
                        @("$SeedPath\Envs\ms-playwright", "$env:LOCALAPPDATA\ms-playwright"))) {
        $src, $dst = $pair
        if (Test-Path $src) { Act "restore $dst" { robocopy $src $dst /E /XJ /NFL /NDL /NP | Out-Null } }
    }
}

# ---- Phase 5: Windows-level state --------------------------------------
if ($Phase -contains 5) {
    Write-Host "`n--- Phase 5: env vars, registry, NSSM services, scheduled tasks ---"
    $skip = 'Path','PSModulePath','ComSpec','windir','OS','TEMP','TMP','USERNAME','PATHEXT',
            'DriverData','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','PROCESSOR_IDENTIFIER',
            'PROCESSOR_LEVEL','PROCESSOR_REVISION','POWERSHELL_DISTRIBUTION_CHANNEL','ChocolateyInstall'
    $mvars = Get-Content "$SeedPath\Core\_sensitive\env_vars_machine.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($mvars) {
        foreach ($p in $mvars.PSObject.Properties) {
            if ($p.Name -in $skip) { continue }
            Act "machine env $($p.Name)" { [Environment]::SetEnvironmentVariable($p.Name, $p.Value, 'Machine') }
        }
    } else { Write-Warning 'env_vars_machine.json not found (still sealed?)' }
    Act 'reg import AI Hub key' { reg import "$SeedPath\Core\_sensitive\registry_aihub.reg" }

    $svcs = Get-Content "$SeedPath\Core\nssm_services.json" -Raw | ConvertFrom-Json
    $nssm = 'C:\src\nssm-2.24\win64\nssm.exe'
    foreach ($p in $svcs.PSObject.Properties) {
        $n = $p.Name; $d = $p.Value
        # Recreate only the dev services; the installed-app set comes from running the installer.
        if ($d.Application -like 'C:\Program Files\AIHub*') { Write-Host "skip installed-app svc: $n"; continue }
        Act "nssm install $n ($($d.Application))" {
            & $nssm install $n $d.Application
            if ($d.AppDirectory)  { & $nssm set $n AppDirectory  $d.AppDirectory }
            if ($d.AppParameters) { & $nssm set $n AppParameters $d.AppParameters }
            & $nssm set $n Start ($(if ($d.StartMode -eq 'Auto') { 'SERVICE_AUTO_START' } else { 'SERVICE_DEMAND_START' }))
        }
    }
    Get-ChildItem "$SeedPath\Core\scheduled_tasks\*.xml" -ErrorAction SilentlyContinue | ForEach-Object {
        $name = $_.BaseName
        Act "register scheduled task $name" {
            Register-ScheduledTask -TaskName $name -TaskPath '\AI\' -Xml (Get-Content $_.FullName -Raw) -Force }
    }
    Write-Host 'MANUAL: map S: (cmdkey + net use - recipe in MACHINE_FACTS.local.md, storage key from portal).'
}

# ---- Phase 6: data + fixtures ------------------------------------------
if ($Phase -contains 6) {
    Write-Host "`n--- Phase 6: fixtures ---"
    foreach ($t in 'leases','AIHub_Demo','YM_DF') {
        if (Test-Path "$SeedPath\Data\temp\$t") { Act "restore C:\temp\$t" { robocopy "$SeedPath\Data\temp\$t" "C:\temp\$t" /E /NFL /NDL /NP | Out-Null } }
    }
    Act 'restore Claude layer (skills/settings/memory)' {
        robocopy "$SeedPath\Core\claude\skills" "$env:USERPROFILE\.claude\skills" /E /NFL /NDL /NP | Out-Null
        Copy-Item "$SeedPath\Core\claude\settings.json" "$env:USERPROFILE\.claude\settings.json" -Force -ErrorAction SilentlyContinue
        robocopy "$SeedPath\Core\claude\projects" "$env:USERPROFILE\.claude\projects" /E /NFL /NDL /NP | Out-Null
    }
}

# ---- Phase 7: verify ----------------------------------------------------
if ($Phase -contains 7) {
    Write-Host @'

--- Phase 7: acceptance (manual, in order) ---
 1. shortcuts\00_Start-Restart_AIHub_Services_V3.bat  -> 14 service windows, no tracebacks
 2. http://localhost:5001 login -> one agent chat, one document search
 3. python test_human\15_Platform_Regression\runner.py  (all-areas gate)
 4. Build check: Build_AIHub_Executables_OneDir_Dev_v3.bat (one service is enough),
    then compile AIHub_Setup_Script_v4_OneDir_Dev.iss with ISCC
 5. If this machine is a SANDBOX: you should have run sandbox_neuter.ps1 BEFORE step 1.
'@
}
