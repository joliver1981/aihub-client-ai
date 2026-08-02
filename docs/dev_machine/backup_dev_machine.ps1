<#
.SYNOPSIS
  Creates a "seed package" backup of everything on this dev machine that git does not cover.

.DESCRIPTION
  Read-only against the machine; writes only to -Destination (default: the private Azure
  Files share). See docs/dev_machine/DEV_MACHINE_PROVISIONING.md for the full plan.

  Tiers:
    Core  (default) — secrets/config/specs/seed files, env-var + registry + NSSM + task
                      exports, conda manifests, git bundles, machine-only trees. MBs–low GBs.
    Data            — tenant/runtime data, corpora, vectors, uploads, C:\temp fixtures. GBs.
    Envs            — whole conda envs + python-bundle + Playwright cache. Tens of GBs.

.EXAMPLE
  .\backup_dev_machine.ps1 -Tier Core
  .\backup_dev_machine.ps1 -Tier Core,Data,Envs -ArchivePassword (Read-Host -AsSecureString)

.NOTES
  The Core tier necessarily contains plaintext secrets (that is what restoring a machine
  requires). Keep the destination private; use -ArchivePassword to seal the sensitive
  subfolder into an AES 7z when 7-Zip is installed.
#>
[CmdletBinding()]
param(
    [string]$Destination = 'S:\DevMachineBackups',
    [ValidateSet('Core', 'Data', 'Envs')]
    [string[]]$Tier = @('Core'),
    [string[]]$CondaEnvs = @('aihub2.1','aihubbuilder','aihubant','aihubvector2','jss',
                             'aihubmcp','aihubcloudgateway','aihub-browseruse','aihubemail','testftp'),
    [string[]]$RepoBundles = @('aihub-client-ai-dev','aihub-email','ai-dca','aihub-accelerator','aihub-llmml'),
    [securestring]$ArchivePassword
)

$ErrorActionPreference = 'Continue'
$Repo     = 'C:\src\aihub-client-ai-dev'
$Conda    = "$env:USERPROFILE\miniconda3"
$Stamp    = Get-Date -Format 'yyyyMMdd_HHmm'
$Dest     = Join-Path $Destination $Stamp
$Failures = [System.Collections.Generic.List[string]]::new()

New-Item -ItemType Directory -Force $Dest | Out-Null
Start-Transcript -Path (Join-Path $Dest 'backup_log.txt') | Out-Null

function Copy-Tree {
    param([string]$Src, [string]$DestRel, [string[]]$ExcludeDirs = @())
    if (-not (Test-Path $Src)) { Write-Host "  skip (missing): $Src"; return }
    $target = Join-Path $Dest $DestRel
    if (Test-Path $Src -PathType Leaf) {
        New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
        Copy-Item $Src $target -Force
        Write-Host "  file: $Src"
        return
    }
    $xd = @(); foreach ($d in $ExcludeDirs) { $xd += '/XD'; $xd += $d }
    robocopy $Src $target /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NFL /NDL /NP @xd | Out-Null
    if ($LASTEXITCODE -ge 8) { $Failures.Add("robocopy($LASTEXITCODE): $Src") ; Write-Warning "FAILED: $Src" }
    else { Write-Host "  tree: $Src" }
}

# ---------------------------------------------------------------- CORE
if ($Tier -contains 'Core') {
    Write-Host "`n=== CORE: repo machine-only files ==="
    # Sensitive items grouped so -ArchivePassword can seal them.
    $sens = 'Core\_sensitive'
    Copy-Tree "$Repo\.env"                          "$sens\repo\.env"
    Copy-Tree "$Repo\data\secrets"                  "$sens\repo\data\secrets"
    Copy-Tree "$Repo\builder_service\data\secrets"  "$sens\repo\builder_service\data\secrets"
    Copy-Tree "$Repo\_build_config.py"              "$sens\repo\_build_config.py"
    Copy-Tree "$Repo\_build_config_client.py"       "$sens\repo\_build_config_client.py"
    Copy-Tree "$Repo\dist\.env"                     "$sens\repo\dist\.env"
    Copy-Tree "C:\src\aihub-email\.env"             "$sens\aihub-email\.env"

    [Environment]::GetEnvironmentVariables('Machine') | ConvertTo-Json |
        Set-Content (New-Item -Force "$Dest\$sens\env_vars_machine.json").FullName
    [Environment]::GetEnvironmentVariables('User') | ConvertTo-Json |
        Set-Content "$Dest\$sens\env_vars_user.json"
    reg export 'HKLM\Software\AI Hub' "$Dest\$sens\registry_aihub.reg" /y | Out-Null

    Write-Host "`n=== CORE: non-secret repo overlay ==="
    foreach ($rel in @('data\portal_registry.json','data\portal_workflows.json',
                       'data\model_overrides.json','data\initial_setup_state.json',
                       'data\onboarding_state.json','tools','run_regression.py','llm_unit_test.py',
                       'e2e_app_tests','tests\e2e','shortcuts','dist\core_tools.yaml',
                       'dist\user_config.py','dist\user_prompts.py','dist\GeneralAgent.pyd',
                       'dist\python-bundle-requirements','dist\static\icons')) {
        Copy-Tree "$Repo\$rel" "Core\repo\$rel"
    }
    # Gitignored machine-specific docs (MACHINE_FACTS, preserved lib patches, ...)
    Get-ChildItem "$Repo\docs\dev_machine\*.local.*" -File -ErrorAction SilentlyContinue |
        ForEach-Object { Copy-Tree $_.FullName "Core\repo\docs\dev_machine\$($_.Name)" }
    # Every .spec anywhere in the tree (11 of 13 are untracked) — tiny files, take all.
    Get-ChildItem $Repo -Recurse -Filter '*.spec' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\(dist|dist_env|build|node_modules|_pkg_cache)\\' } |
        ForEach-Object {
            $rel = $_.FullName.Substring($Repo.Length + 1)
            Copy-Tree $_.FullName "Core\repo\$rel"
        }

    Write-Host "`n=== CORE: NSSM service definitions ==="
    $svc = @{}
    foreach ($s in Get-CimInstance Win32_Service | Where-Object { $_.PathName -match 'nssm' }) {
        $p = "HKLM:\SYSTEM\CurrentControlSet\Services\$($s.Name)\Parameters"
        if (Test-Path $p) {
            $i = Get-ItemProperty $p
            $svc[$s.Name] = @{ StartMode = $s.StartMode; Application = $i.Application
                               AppDirectory = $i.AppDirectory; AppParameters = $i.AppParameters }
        }
    }
    $svc | ConvertTo-Json -Depth 4 | Set-Content "$Dest\Core\nssm_services.json"

    Write-Host "`n=== CORE: scheduled tasks ==="
    New-Item -ItemType Directory -Force "$Dest\Core\scheduled_tasks" | Out-Null
    Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\AI\' -or $_.TaskName -in 'OpenClaw Gateway','TakeScreenshot' } |
        ForEach-Object {
            $safe = ($_.TaskName -replace '[^\w\- ]', '_')
            Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath |
                Set-Content "$Dest\Core\scheduled_tasks\$safe.xml"
        }

    Write-Host "`n=== CORE: conda manifests (documentation of every live env) ==="
    New-Item -ItemType Directory -Force "$Dest\Core\conda_manifests" | Out-Null
    foreach ($e in $CondaEnvs) {
        $py = "$Conda\envs\$e\python.exe"
        if (Test-Path $py) {
            & $py -m pip freeze --all 2>$null | Set-Content "$Dest\Core\conda_manifests\pip_freeze_$e.txt"
            & "$Conda\Scripts\conda.exe" list -p "$Conda\envs\$e" --explicit 2>$null |
                Set-Content "$Dest\Core\conda_manifests\conda_explicit_$e.txt"
        } else { Write-Host "  skip env (missing): $e" }
    }

    Write-Host "`n=== CORE: git bundles ==="
    New-Item -ItemType Directory -Force "$Dest\Core\git_bundles" | Out-Null
    foreach ($r in $RepoBundles) {
        $path = "C:\src\$r"
        if (Test-Path "$path\.git") {
            git -C $path bundle create "$Dest\Core\git_bundles\$r.bundle" --all 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { $Failures.Add("git bundle: $r") } else { Write-Host "  bundle: $r" }
        } else { Write-Host "  skip (no .git): $r" }
    }

    Write-Host "`n=== CORE: machine-only trees ==="
    Copy-Tree 'C:\src\ai-colab'            'Core\src\ai-colab' @('node_modules', '.git')
    Copy-Tree 'C:\src\dummy-service'       'Core\src\dummy-service'
    Copy-Tree 'C:\src\Inno_Build_Scripts'  'Core\src\Inno_Build_Scripts'
    Copy-Tree 'C:\src\nssm-2.24'           'Core\src\nssm-2.24'
    Copy-Tree 'C:\scripts'                 'Core\scripts'

    Write-Host "`n=== CORE: agent layer (.claude skills/memory, .openclaw) ==="
    Copy-Tree "$env:USERPROFILE\.claude\settings.json" 'Core\claude\settings.json'
    Copy-Tree "$env:USERPROFILE\.claude\skills"        'Core\claude\skills'
    Get-ChildItem "$env:USERPROFILE\.claude\projects" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object {
            if (Test-Path "$($_.FullName)\memory") { Copy-Tree "$($_.FullName)\memory" "Core\claude\projects\$($_.Name)\memory" }
        }
    Copy-Tree "$env:USERPROFILE\.openclaw" 'Core\openclaw' @('node_modules','.git','logs','cache','browser')

    Write-Host "`n=== CORE: provenance ==="
    $apps = @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
              'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*') |
        ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } |
        Where-Object DisplayName | Sort-Object DisplayName -Unique |
        Select-Object DisplayName, DisplayVersion, Publisher
    $apps | ConvertTo-Json | Set-Content "$Dest\Core\installed_software.json"
    $vm = $null
    try { $vm = Invoke-RestMethod -Headers @{Metadata='true'} -TimeoutSec 5 `
              -Uri 'http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01' } catch {}
    @{ capturedUtc = (Get-Date).ToUniversalTime().ToString('o'); host = $env:COMPUTERNAME
       user = $env:USERNAME; os = (Get-CimInstance Win32_OperatingSystem).Caption
       vmSize = $vm.vmSize; region = $vm.location; resourceGroup = $vm.resourceGroupName
       tiers = $Tier -join ','; condaEnvs = $CondaEnvs -join ','
    } | ConvertTo-Json | Set-Content "$Dest\Core\provenance.json"
}

# ---------------------------------------------------------------- DATA
if ($Tier -contains 'Data') {
    Write-Host "`n=== DATA: repo runtime/tenant data ==="
    Copy-Tree "$Repo\data" 'Data\repo\data' @('secrets')   # secrets already in Core\_sensitive
    foreach ($rel in @('knowledge_files','chroma_db','agent_sessions','workflows','uploads',
                       'exports','command_center_service\data','workflow_builder_sessions',
                       'automations\_pkg_cache')) {
        Copy-Tree "$Repo\$rel" "Data\repo\$rel"
    }
    Copy-Tree "$Repo\builder_service\data" 'Data\repo\builder_service\data' @('secrets')
    Get-ChildItem "$Repo\agent_environments" -Directory -Filter 'tenant_*' -ErrorAction SilentlyContinue |
        ForEach-Object { Copy-Tree $_.FullName "Data\repo\agent_environments\$($_.Name)" }
    Get-ChildItem "$Repo\automations" -Directory -Filter 'tenant_*' -ErrorAction SilentlyContinue |
        ForEach-Object { Copy-Tree $_.FullName "Data\repo\automations\$($_.Name)" }

    Write-Host "`n=== DATA: fixture sets outside the repo ==="
    Copy-Tree 'C:\temp\leases'     'Data\temp\leases'
    Copy-Tree 'C:\temp\AIHub_Demo' 'Data\temp\AIHub_Demo'
    Copy-Tree 'C:\temp\YM_DF'      'Data\temp\YM_DF'
}

# ---------------------------------------------------------------- ENVS
if ($Tier -contains 'Envs') {
    Write-Host "`n=== ENVS: conda environments (restore to the IDENTICAL path) ==="
    foreach ($e in $CondaEnvs) { Copy-Tree "$Conda\envs\$e" "Envs\conda\$e" }
    Copy-Tree "$Repo\dist\python-bundle"              'Envs\dist\python-bundle'
    Copy-Tree "$Repo\agent_environments\python-bundle" 'Envs\repo\agent_environments\python-bundle'
    Copy-Tree "$env:LOCALAPPDATA\ms-playwright"        'Envs\ms-playwright'
}

# ------------------------------------------------- seal sensitive folder
$sensPath = Join-Path $Dest 'Core\_sensitive'
if ($ArchivePassword -and (Test-Path $sensPath)) {
    $sz = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($sz) {
        $plain = [System.Net.NetworkCredential]::new('', $ArchivePassword).Password
        & $sz a -t7z -mhe=on "-p$plain" "$Dest\Core\_sensitive.7z" "$sensPath\*" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Remove-Item $sensPath -Recurse -Force
            Write-Host "sensitive folder sealed -> Core\_sensitive.7z"
        } else { $Failures.Add('7z seal failed - plaintext _sensitive left in place') }
    } else { Write-Warning '7-Zip not found - _sensitive left as plaintext on the private share.' }
} elseif (Test-Path $sensPath) {
    Write-Warning 'No -ArchivePassword: Core\_sensitive is PLAINTEXT (private share is the perimeter).'
}

Write-Host "`n=== SUMMARY ==="
$bytes = (Get-ChildItem $Dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
Write-Host ("Backup size: {0:N2} GB at {1}" -f ($bytes/1GB), $Dest)
if ($Failures.Count) { Write-Warning ("{0} FAILURES:`n  " -f $Failures.Count) ; $Failures | ForEach-Object { Write-Warning "  $_" } }
else { Write-Host 'All items copied cleanly.' }
Stop-Transcript | Out-Null
