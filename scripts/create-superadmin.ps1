<#
Crea el primer superadmin de la web (solo en esta PC; nunca desde la web ni con contrasena por defecto).
Pide la contrasena en la consola sin mostrarla. Asigna al superadmin los dispositivos ya aprobados.

  .\scripts\create-superadmin.ps1
  .\scripts\create-superadmin.ps1 -Username laura -Name "Laura"
#>
param(
    [string]$Username,
    [string]$Name
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python of the project venv was not found: $python"
}

$arguments = @('-m', 'backend.app.users.cli', 'create-superadmin')
if ($Username) { $arguments += @('--username', $Username) }
if ($PSBoundParameters.ContainsKey('Name')) { $arguments += @('--name', $Name) }

Push-Location $projectRoot
try {
    & $python @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
