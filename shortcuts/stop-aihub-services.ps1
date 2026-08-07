# stop-aihub-services.ps1 — deterministic stop for all AI Hub AI-DEV services.
#
# Kill layers, in order:
#   0. Record the current owning PID of every service port (written to the state
#      file so verify-aihub-services.ps1 can prove the restart produced NEW pids).
#   A. Window-title kills (fast path: kills the cmd window AND its python child
#      while titles still say "AIHub-DEV ..."). PS sweep uses a *contains* match
#      so retitled variants ("Select ...", "Administrator: ...") are still caught.
#   B. Command-line sweep for the named service scripts (catches orphaned pythons
#      whose window is gone/retitled; the only layer that can see the two
#      port-less pollers app_doc_job_q.py / app_jss_main.py).
#      NOTE: "python main.py" services CANNOT be identified by command line —
#      builder_service/builder_data/command_center/browser_use all look identical.
#      They are covered by the port layer below.
#   C. Port-ownership kill: whatever process owns a service port dies, no matter
#      what its window title or command line looks like. This is the layer that
#      makes the stop deterministic.
#   D. VERIFY: poll until every service port is FREE and the pollers are gone.
#      Exit 1 if anything survives — callers must NOT start services in that case.
#
# Exit codes: 0 = all ports free, all pollers dead. 1 = something survived.
# Windows PowerShell 5.1 compatible.

param(
    [string]$StatePath = "$env:TEMP\aihub-dev-restart-state.json",
    [int]$VerifyTimeoutSec = 20
)

$ErrorActionPreference = 'Continue'

# One entry per service that LISTENS on a port (HOST_PORT=5001 scheme, see CommonUtils.py)
$PortServices = @(
    @{ Port = 5001; Name = 'Main App (wsgi.py)' }
    @{ Port = 5011; Name = 'Document API (wsgi_doc_api.py)' }
    @{ Port = 5031; Name = 'Vector API (wsgi_vector_api.py)' }
    @{ Port = 5041; Name = 'Agent API (wsgi_agent_api.py)' }
    @{ Port = 5051; Name = 'Knowledge API (wsgi_knowledge_api.py)' }
    @{ Port = 5061; Name = 'Executor Service (wsgi_executor_service.py)' }
    @{ Port = 5071; Name = 'MCP Gateway (app_mcp_gateway.py)' }
    @{ Port = 5081; Name = 'Cloud Gateway (app_cloud_gateway.py)' }
    @{ Port = 5091; Name = 'Command Center (command_center_service\main.py)' }
    @{ Port = 5101; Name = 'Browser Use (browser_use_service\main.py)' }
    @{ Port = 8100; Name = 'Builder Service (builder_service\main.py)' }
    @{ Port = 8200; Name = 'Builder Data (builder_data\main.py)' }
)

# Services with NO listener — only findable by command line
$PollerPattern = 'app_doc_job_q\.py|app_jss_main\.py'

# All uniquely-named service scripts (for the orphan sweep). Deliberately NOT
# matching bare "main.py" — too generic; the port layer owns those services.
$SweepPattern = 'wsgi\.py|wsgi_doc_api\.py|app_doc_job_q\.py|app_jss_main\.py|wsgi_vector_api\.py|wsgi_agent_api\.py|wsgi_knowledge_api\.py|wsgi_executor_service\.py|app_mcp_gateway\.py|app_cloud_gateway\.py'

# --- Self-protection: never kill this script's own process tree -------------
$ProtectedPids = New-Object System.Collections.Generic.HashSet[int]
$walk = $PID
for ($i = 0; $i -lt 20 -and $walk -gt 0; $i++) {
    if (-not $ProtectedPids.Add([int]$walk)) { break }
    $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$walk" -ErrorAction SilentlyContinue
    if (-not $pp) { break }
    $walk = [int]$pp.ParentProcessId
}

$script:AccessDenied = $false

function Kill-Tree {
    param([int]$TargetPid, [string]$Why)
    if ($TargetPid -le 4) { Write-Host "  [SKIP]   PID $TargetPid is a system process, cannot kill ($Why)"; return }
    if ($ProtectedPids.Contains($TargetPid)) { Write-Host "  [skip]   PID $TargetPid is this script's own tree ($Why)"; return }
    $out = (& taskkill /PID $TargetPid /T /F 2>&1) | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [killed] PID $TargetPid  ($Why)"
    } elseif ($out -match 'not found') {
        Write-Host "  [gone]   PID $TargetPid  ($Why)"
    } else {
        Write-Host "  [FAILED] PID $TargetPid  ($Why): $($out.Trim())"
        if ($out -match 'denied') { $script:AccessDenied = $true }
    }
}

# Kill a service process; if its parent is a cmd.exe window, kill that instead
# (the /T tree kill takes the python down with it and the window closes too).
function Kill-ServiceProcess {
    param([int]$TargetPid, [string]$Why)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetPid" -ErrorAction SilentlyContinue
    if ($proc) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent -and $parent.Name -ieq 'cmd.exe' -and -not $ProtectedPids.Contains([int]$parent.ProcessId)) {
            Kill-Tree -TargetPid ([int]$parent.ProcessId) -Why "cmd window of: $Why"
            Start-Sleep -Milliseconds 200
            if (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue) { Kill-Tree -TargetPid $TargetPid -Why $Why }
            return
        }
    }
    Kill-Tree -TargetPid $TargetPid -Why $Why
}

function Get-PortOwners {
    param([int]$Port)
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

Write-Host ''
Write-Host 'AI Hub AI-DEV deterministic stop'
Write-Host '--------------------------------'

# --- Phase 0: record current port owners (pre-kill) for the verify script ---
foreach ($svc in $PortServices) {
    $owners = Get-PortOwners -Port $svc.Port
    $svc.OldPid = if ($owners.Count -gt 0) { [int]$owners[0] } else { $null }
}

# --- Phase A: window-title kills (kills cmd + python together) --------------
Write-Host '[A] Killing AIHub-DEV windows by title...'
& taskkill /FI "WINDOWTITLE eq AIHub-DEV*" /T /F  2>&1 | Out-Null
& taskkill /FI "WINDOWTITLE eq Administrator:*AIHub-DEV*" /T /F  2>&1 | Out-Null
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like '*AIHub-DEV*' -and -not $ProtectedPids.Contains($_.Id) } |
    ForEach-Object { Kill-Tree -TargetPid $_.Id -Why "window '$($_.MainWindowTitle)'" }

Start-Sleep -Milliseconds 500

# --- Phase B: command-line sweep for named service scripts ------------------
Write-Host '[B] Sweeping orphaned service pythons by command line...'
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match $SweepPattern -and -not $ProtectedPids.Contains([int]$_.ProcessId) } |
    ForEach-Object { Kill-ServiceProcess -TargetPid ([int]$_.ProcessId) -Why ($_.CommandLine -replace '\s+', ' ').Trim() }

# --- Phase C: port-ownership kill (the deterministic layer) -----------------
Write-Host '[C] Killing by port ownership...'
foreach ($svc in $PortServices) {
    foreach ($owner in (Get-PortOwners -Port $svc.Port)) {
        $owner = [int]$owner
        if ($owner -le 4) {
            Write-Host "  [FAILED] port $($svc.Port) is owned by SYSTEM PID $owner - cannot kill. Manual intervention required."
            continue
        }
        # If the owner is a Windows service (e.g. an NSSM AIHub* install), stop the
        # service first so its manager does not instantly restart the process.
        $winSvc = Get-CimInstance Win32_Service -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($winSvc) {
            Write-Host "  [service] port $($svc.Port) owned by Windows service '$($winSvc.Name)' (PID $owner) - stopping service..."
            Stop-Service -Name $winSvc.Name -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        }
        if (Get-Process -Id $owner -ErrorAction SilentlyContinue) {
            Kill-ServiceProcess -TargetPid $owner -Why "owns port $($svc.Port) [$($svc.Name)]"
        }
    }
}

# --- Phase D: VERIFY every port is free and the pollers are gone ------------
Write-Host "[D] Verifying all service ports are free (up to $VerifyTimeoutSec s)..."
$deadline = (Get-Date).AddSeconds($VerifyTimeoutSec)
$stillOwned = @()
$stillPolling = @()
do {
    Start-Sleep -Milliseconds 500
    $stillOwned = @($PortServices | Where-Object { (Get-PortOwners -Port $_.Port).Count -gt 0 })
    $stillPolling = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match $PollerPattern -and -not $ProtectedPids.Contains([int]$_.ProcessId) })
    # Re-kill stragglers each pass (slow shutdown handlers, respawned children)
    foreach ($svc in $stillOwned) {
        foreach ($owner in (Get-PortOwners -Port $svc.Port)) { Kill-ServiceProcess -TargetPid ([int]$owner) -Why "still owns port $($svc.Port)" }
    }
    foreach ($p in $stillPolling) { Kill-ServiceProcess -TargetPid ([int]$p.ProcessId) -Why 'poller still running' }
} while ((($stillOwned.Count -gt 0) -or ($stillPolling.Count -gt 0)) -and ((Get-Date) -lt $deadline))

# --- Write state file for verify-aihub-services.ps1 -------------------------
$state = [pscustomobject]@{
    stoppedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
    ports        = @($PortServices | ForEach-Object { [pscustomobject]@{ port = $_.Port; name = $_.Name; oldPid = $_.OldPid } })
}
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $StatePath -Encoding UTF8

# --- Verdict ----------------------------------------------------------------
Write-Host ''
if ($stillOwned.Count -eq 0 -and $stillPolling.Count -eq 0) {
    Write-Host 'STOP OK: all service ports are FREE and both pollers are down.'
    exit 0
}

Write-Host '*** STOP FAILED - the following survived every kill layer: ***'
foreach ($svc in $stillOwned) {
    foreach ($owner in (Get-PortOwners -Port $svc.Port)) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
        $ownerName = ''
        if ($p) { $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue; $ownerName = "$($o.User)" }
        Write-Host ("  port {0}  PID {1}  {2}  user={3}  cmd={4}" -f $svc.Port, $owner, $svc.Name, $ownerName, $p.CommandLine)
    }
}
foreach ($p in $stillPolling) {
    Write-Host ("  (no port)  PID {0}  cmd={1}" -f $p.ProcessId, $p.CommandLine)
}
if ($script:AccessDenied) {
    Write-Host '  NOTE: at least one kill was ACCESS DENIED - an elevated (Administrator) process'
    Write-Host '        cannot be killed from a non-elevated window. Re-run this script as Administrator.'
}
exit 1
