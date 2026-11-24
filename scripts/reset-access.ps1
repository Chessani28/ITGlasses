<#
Recupera el acceso a la web desde esta PC (cualquier rol, incluido el superadmin).

  .\scripts\reset-access.ps1 -List                        # usuarios, rol, estado y bloqueo
  .\scripts\reset-access.ps1 -Username laura            # nueva contrasena (cierra sus sesiones y desbloquea)
  .\scripts\reset-access.ps1 -Username ana -Temporary     # contrasena temporal: debera cambiarla al entrar
  .\scripts\reset-access.ps1 -Username laura -Unlock    # solo quitar el bloqueo por intentos
  .\scripts\reset-access.ps1 -Username laura -Enable    # reactivar una cuenta desactivada
#>
param(
    [string]$Username,
    [switch]$List,
    [switch]$Unlock,
    [switch]$Enable,
    [switch]$Temporary
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python of the project venv was not found: $python"
}

if ($List) {
    $arguments = @('list')
} elseif ($Unlock) {
    $arguments = @('unlock')
} elseif ($Enable) {
    $arguments = @('enable')
} else {
    $arguments = @('reset-password')
    if ($Temporary) { $arguments += '--temporary' }
}
if ($Username -and -not $List) { $arguments += @('--username', $Username) }

Push-Location $projectRoot
try {
    & $python -m backend.app.users.cli @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
