$port = 8100

$connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue

foreach ($conn in $connections) {
    Write-Host "Killing PID $($conn.OwningProcess)"
    Stop-Process -Id $conn.OwningProcess -Force
}

Write-Host "Done."