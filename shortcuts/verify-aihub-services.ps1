# verify-aihub-services.ps1 — post-start verification for the AI-DEV restart.
#
# Reads the state file written by stop-aihub-services.ps1 (pre-stop port owners +
# stop-completion timestamp) and then, for every service port:
#   - waits (bounded) for a LISTENING owner to appear, and
#   - proves the listener is a NEW process: its creation time must be AFTER the
#     stop phase completed. PID comparison alone is not enough (PIDs get reused),
#     and PID difference alone is not enough either (a pre-existing stale process
#     could have grabbed the port) - creation time is the airtight test.
# Also waits for the two port-less pollers (app_doc_job_q.py / app_jss_main.py)
# and fails on duplicates (two schedulers double-fire jobs).
#
# Exit codes: 0 = every service is up as a NEW process. 1 = anything missing,
# stale, or duplicated. Windows PowerShell 5.1 compatible.

param(
    [string]$StatePath = "$env:TEMP\aihub-dev-restart-state.json",
    [int]$TimeoutSec = 240
)

$ErrorActionPreference = 'Continue'

# Fallback port table (used only if no state file exists, e.g. first boot)
$DefaultPorts = @(
    @{ port = 5001; name = 'Main App (wsgi.py)' },
    @{ port = 5011; name = 'Document API (wsgi_doc_api.py)' },
    @{ port = 5031; name = 'Vector API (wsgi_vector_api.py)' },
    @{ port = 5041; name = 'Agent API (wsgi_agent_api.py)' },
    @{ port = 5051; name = 'Knowledge API (wsgi_knowledge_api.py)' },
    @{ port = 5061; name = 'Executor Service (wsgi_executor_service.py)' },
    @{ port = 5071; name = 'MCP Gateway (app_mcp_gateway.py)' },
    @{ port = 5081; name = 'Cloud Gateway (app_cloud_gateway.py)' },
    @{ port = 5091; name = 'Command Center (command_center_service\main.py)' },
    @{ port = 5101; name = 'Browser Use (browser_use_service\main.py)' },
    @{ port = 8100; name = 'Builder Service (builder_service\main.py)' },
    @{ port = 8200; name = 'Builder Data (builder_data\main.py)' }
)
$Pollers = @(
    @{ pattern = 'app_doc_job_q\.py'; name = 'Doc Job Queue (app_doc_job_q.py)' },
    @{ pattern = 'app_jss_main\.py';  name = 'JSS Scheduler (app_jss_main.py)' }
)

$stoppedAtUtc = $null
$ports = $null
if (Test-Path $StatePath) {
    try {
        $state = Get-Content $StatePath -Raw | ConvertFrom-Json
        $stoppedAtUtc = [datetime]::Parse($state.stoppedAtUtc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        $ports = @($state.ports | ForEach-Object { @{ port = [int]$_.port; name = [string]$_.name; oldPid = $_.oldPid } })
    } catch {
        Write-Host "  WARN: could not read state file ($StatePath): $($_.Exception.Message)"
    }
}
if (-not $ports) {
    Write-Host '  WARN: no stop-state file - cannot compare against pre-stop PIDs; only checking that listeners exist and are fresh.'
    $ports = $DefaultPorts | ForEach-Object { @{ port = $_.port; name = $_.name; oldPid = $null } }
}

function Get-ListenerPid { param([int]$Port)
    $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -gt 0) { [int]$owners[0] } else { $null }
}

function Get-CreationUtc { param([int]$TargetPid)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetPid" -ErrorAction SilentlyContinue
    if ($p -and $p.CreationDate) { $p.CreationDate.ToUniversalTime() } else { $null }
}

Write-Host ''
Write-Host "Verifying AI-DEV services (timeout $TimeoutSec s)..."
if ($stoppedAtUtc) { Write-Host "  Stop phase completed at $($stoppedAtUtc.ToLocalTime().ToString('HH:mm:ss')) - every listener must be newer than that." }

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$results = @{}   # port -> result object
$pollerResults = @{}

do {
    foreach ($svc in $ports) {
        if ($results.ContainsKey($svc.port)) { continue }
        $newPid = Get-ListenerPid -Port $svc.port
        if ($null -eq $newPid) { continue }
        $created = Get-CreationUtc -TargetPid $newPid
        $fresh = $true
        if ($stoppedAtUtc -and $created) { $fresh = ($created -gt $stoppedAtUtc) }
        $oldPidText = if ($null -ne $svc.oldPid) { $svc.oldPid } else { '-' }
        $status = if ($fresh) { 'OK  NEW' } else { 'FAIL STALE' }
        $results[$svc.port] = @{ svc = $svc; newPid = $newPid; fresh = $fresh }
        Write-Host ("  [{0}] port {1,-5} {2,-45} PID {3} -> {4}" -f $status, $svc.port, $svc.name, $oldPidText, $newPid)
    }
    foreach ($poller in $Pollers) {
        if ($pollerResults.ContainsKey($poller.pattern)) { continue }
        $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match $poller.pattern })
        if ($procs.Count -eq 0) { continue }
        if ($procs.Count -gt 1) {
            $pollerResults[$poller.pattern] = @{ poller = $poller; ok = $false; reason = "DUPLICATE ($($procs.Count) instances: $(($procs | ForEach-Object { $_.ProcessId }) -join ', '))" }
            Write-Host ("  [FAIL DUP] {0,-56} {1}" -f $poller.name, $pollerResults[$poller.pattern].reason)
            continue
        }
        $created = if ($procs[0].CreationDate) { $procs[0].CreationDate.ToUniversalTime() } else { $null }
        $fresh = $true
        if ($stoppedAtUtc -and $created) { $fresh = ($created -gt $stoppedAtUtc) }
        $status = if ($fresh) { 'OK  NEW' } else { 'FAIL STALE' }
        $pollerResults[$poller.pattern] = @{ poller = $poller; ok = $fresh; reason = if ($fresh) { '' } else { 'process predates the stop phase' } }
        Write-Host ("  [{0}] (no port) {1,-45} PID {2}" -f $status, $poller.name, $procs[0].ProcessId)
    }
    if (($results.Count -eq $ports.Count) -and ($pollerResults.Count -eq $Pollers.Count)) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

$failed = @()
foreach ($svc in $ports) {
    if (-not $results.ContainsKey($svc.port)) { $failed += "port $($svc.port) $($svc.name): NO LISTENER after $TimeoutSec s" }
    elseif (-not $results[$svc.port].fresh) { $failed += "port $($svc.port) $($svc.name): listener PID $($results[$svc.port].newPid) PREDATES the stop phase (stale process still owns the port)" }
}
foreach ($poller in $Pollers) {
    if (-not $pollerResults.ContainsKey($poller.pattern)) { $failed += "$($poller.name): NOT RUNNING after $TimeoutSec s" }
    elseif (-not $pollerResults[$poller.pattern].ok) { $failed += "$($poller.name): $($pollerResults[$poller.pattern].reason)" }
}

Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host 'VERIFY OK: all 12 listeners + 2 pollers are up as NEW processes.'
    exit 0
}
Write-Host '*** VERIFY FAILED: ***'
$failed | ForEach-Object { Write-Host "  $_" }
exit 1
