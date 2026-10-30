$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$stopped = $false
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -like "*backend.run*" -or $_.CommandLine -like "*uvicorn*backend.app.main*") -and $_.CommandLine -like "*$projectRoot*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
    Write-Host "Stopped ITGlasses backend PID $($_.ProcessId)"
    $stopped = $true
}
if (-not $stopped) { Write-Host "No ITGlasses backend process found." }
