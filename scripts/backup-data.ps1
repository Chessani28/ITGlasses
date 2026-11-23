<#
Respaldo de las bases de ITGlasses en storage\backups\<fecha>-manual-...\ : dispositivos (usuarios, sesiones,
auditoria) e historial de chats, cada una verificada, con manifest.json. Se puede hacer con el backend corriendo.

  .\scripts\backup-data.ps1                      # dispositivos e historial
  .\scripts\backup-data.ps1 -IncludeKnowledge    # tambien el indice de manuales (no los archivos originales)
  .\scripts\backup-data.ps1 -List                # respaldos existentes (manuales y diarios)

Restaurar un archivo del respaldo (con el backend detenido):
  .\scripts\restore-pairing-backup.ps1 storage\backups\...\devices.sqlite3
  .\scripts\restore-pairing-backup.ps1 -History storage\backups\...\history.sqlite3
#>
param(
    [switch]$IncludeKnowledge,
    [switch]$List
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python of the project venv was not found: $python"
}

$arguments = @('-m', 'backend.app.backups')
if ($List) { $arguments += '--list' }
if ($IncludeKnowledge) { $arguments += '--include-knowledge' }

Push-Location $projectRoot
try {
    & $python @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
