<#
Restaura la base de dispositivos (storage\pairing\devices.sqlite3) o, con -History, el historial de chats
(storage\history\history.sqlite3) desde un respaldo. Deten el backend antes: .\scripts\stop-backend.ps1

  .\scripts\restore-pairing-backup.ps1 -List
  .\scripts\restore-pairing-backup.ps1 storage\pairing\backups\devices-v1-....sqlite3
  .\scripts\restore-pairing-backup.ps1 -History -List
  .\scripts\restore-pairing-backup.ps1 -History storage\backups\...\history.sqlite3
#>
param(
    [Parameter(Position = 0)]
    [string]$Backup,
    [switch]$List,
    [switch]$History
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python of the project venv was not found: $python"
}

Push-Location $projectRoot
try {
    $kind = @()
    if ($History) { $kind = @('--history') }
    if ($List -or [string]::IsNullOrWhiteSpace($Backup)) {
        & $python -m backend.app.pairing.restore @kind --list
    } else {
        & $python -m backend.app.pairing.restore @kind $Backup
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
