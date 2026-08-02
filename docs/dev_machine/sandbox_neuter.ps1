<#
.SYNOPSIS
  Run FIRST BOOT on any clone of the dev machine, BEFORE starting services.
  Disables everything that would collide with the primary machine's shared state.

.DESCRIPTION
  A clone wakes up believing it is the primary: the email NSSM service auto-starts and
  polls the SHARED mailbox; the job scheduler (if launched) polls the SHARED Azure SQL
  config DB and double-executes every scheduled job; \AI\ scheduled tasks re-seed shared
  test databases. This script disables all of that. See plan §5.

  Refuses to run on the primary machine (marker file) unless -Force.

.EXAMPLE
  .\sandbox_neuter.ps1 -Execute -RenameTo aihubdev-clone1
#>
[CmdletBinding()]
param(
    [switch]$Execute,
    [string]$RenameTo,
    [switch]$Force
)

$marker = "$env:USERPROFILE\.aihub_primary_machine"
if ((Test-Path $marker) -and -not $Force) {
    throw "Primary-machine marker found ($marker). This looks like the PRIMARY dev machine - refusing to neuter it. Use -Force only if you know better."
}
function Act { param([string]$What, [scriptblock]$Do)
    if ($Execute) { Write-Host "DO  : $What"; & $Do } else { Write-Host "PLAN: $What (dry run - add -Execute)" }
}

Write-Host "=== Neutering clone: services ==="
# Auto-start dev services that talk to shared resources + the installed-app service set.
$targets = @('AIHubEmail', 'DummyService') + (
    Get-CimInstance Win32_Service |
        Where-Object { $_.PathName -like 'C:\Program Files\AIHub\*' } |
        Select-Object -ExpandProperty Name)
foreach ($s in $targets | Sort-Object -Unique) {
    if (Get-Service $s -ErrorAction SilentlyContinue) {
        Act "stop + disable service $s" {
            Stop-Service $s -Force -ErrorAction SilentlyContinue
            Set-Service $s -StartupType Disabled
        }
    }
}

Write-Host "`n=== Neutering clone: scheduled tasks ==="
Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\AI\' -or $_.TaskName -in 'OpenClaw Gateway','TakeScreenshot' } |
    ForEach-Object {
        $t = $_
        Act "disable task $($t.TaskPath)$($t.TaskName)" { $t | Disable-ScheduledTask | Out-Null }
    }

if ($RenameTo) { Act "rename computer -> $RenameTo (reboot required)" { Rename-Computer -NewName $RenameTo -Force } }

Write-Host @'

=== Remaining MANUAL guardrails (cannot be safely automated) ===
 1. JOB SCHEDULER: do NOT launch the JSS window (or close it after the start bat opens it)
    until the clone points at its own DB copy - it polls the SHARED config DB and will
    double-execute scheduled jobs. For full isolation: az sql db copy (MACHINE_FACTS) and
    set DATABASE_NAME/CLOUD_DATABASE_NAME in .env to the copy.
 2. LangSmith: change LANGSMITH_PROJECT (machine env var) so clone traces do not pollute
    the primary project.
 3. LLM keys are shared - fine for experiments, but heavy runs share the same rate limits
    and bill.
 4. Demo/test SQL (AIRDB/ERPDB on the test host) is SHARED - destructive experiments there
    hit the primary's test data too. Snapshot/reseed scripts: C:\scripts, reseed_airdb2.py.
 5. Email flows: with AIHubEmail disabled, inbound-email tests will (correctly) not run here.
'@
