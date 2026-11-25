# Arranca ITGlasses completo: Ollama (con su modelo de embeddings), el backend y Caddy para los celulares.
# Reutiliza lo que ya esta sano. Nunca detiene procesos ajenos: si otro Ollama ocupa el puerto sin los modelos,
# lo explica y se detiene. No cambia la configuracion de red: si la IP de Caddy ya no es de esta PC, avisa.
#   .\scripts\start-all.ps1
param([string]$CaddyRoot = 'C:\ITGlasses-Caddy')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
. "$PSScriptRoot\itglasses-services.ps1"
Set-Location $projectRoot
$logs = Join-Path $env:LOCALAPPDATA 'ITGlasses\logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$summary = [ordered]@{}

# --- 1/3 Ollama -----------------------------------------------------------------------------------------------
Write-Host '1/3 Ollama...'
$required = @((Get-ITGlassesChatModel $projectRoot), $script:ITGlassesEmbeddingModel)
$models = Get-ITGlassesOllamaModels
if ($null -eq $models) {
    if (Test-ITGlassesPort 11434) {
        Write-Warning 'El puerto 11434 esta ocupado pero Ollama no responde. Cierra ese proceso y vuelve a correr start-all.'
        exit 1
    }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $PSScriptRoot 'start-ollama.ps1')
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "ollama-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logs "ollama-$stamp.err.log") | Out-Null
    for ($i = 0; $i -lt 60 -and $null -eq $models; $i++) {
        Start-Sleep -Seconds 2
        $models = Get-ITGlassesOllamaModels
    }
    if ($null -eq $models) {
        Write-Warning "Ollama no arranco en 2 minutos. Revisa $logs\ollama-$stamp.err.log"
        exit 1
    }
    $summary['Ollama'] = 'OK (arrancado)'
} else {
    $summary['Ollama'] = 'OK (ya corria)'
}
$missing = @(Get-ITGlassesMissingModels -Available $models -Required $required)
if ($missing.Count -gt 0) {
    Write-Warning ("El Ollama que esta corriendo no tiene: {0}. Probablemente es otro Ollama (por ejemplo la app de " +
        "escritorio). Cierralo (icono de la bandeja > Quit) y vuelve a correr start-all.") -f ($missing -join ', ')
    exit 1
}

# --- 2/3 Backend ----------------------------------------------------------------------------------------------
Write-Host '2/3 Backend...'
& (Join-Path $PSScriptRoot 'ensure-backend.ps1') -Provider ollama
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'El backend no arranco; revisa los logs que indica ensure-backend.'
    exit 1
}
$summary['Backend'] = 'OK  http://127.0.0.1:8000/'

# --- 3/3 Caddy ------------------------------------------------------------------------------------------------
Write-Host '3/3 Caddy...'
$caddyfile = Join-Path $CaddyRoot 'Caddyfile'
$caddy = Join-Path $CaddyRoot 'bin\caddy.exe'
$bind = Get-ITGlassesCaddyBind -Path $caddyfile
$addresses = @(Get-ITGlassesLanAddresses)
if (-not $bind -or -not (Test-Path -LiteralPath $caddy)) {
    $summary['Caddy'] = "NO: falta $caddyfile o $caddy (los celulares no podran conectar)"
} elseif ($bind -notin $addresses) {
    $summary['Caddy'] = "NO: la IP de Caddy ($bind) ya no es de esta PC (ahora: $($addresses -join ', '))"
    Write-Warning ("La IP de esta PC cambio. Lo ideal es reservar la IP en el router. Para seguir ahora: " +
        ".venv\Scripts\python scripts\caddy-config.py --output $caddyfile --bind <IP nueva> y vuelve a correr start-all.")
} elseif (Test-ITGlassesPort 8443) {
    $summary['Caddy'] = "OK (ya corria)  https://${bind}:8443"
} else {
    Start-Process -FilePath $caddy -ArgumentList ('run --config "{0}" --adapter caddyfile' -f $caddyfile) `
        -WorkingDirectory $CaddyRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "caddy-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logs "caddy-$stamp.err.log") | Out-Null
    for ($i = 0; $i -lt 15 -and -not (Test-ITGlassesPort 8443); $i++) { Start-Sleep -Seconds 1 }
    $summary['Caddy'] = if (Test-ITGlassesPort 8443) { "OK (arrancado)  https://${bind}:8443" } else {
        "NO: no arranco; revisa $logs\caddy-$stamp.err.log" }
}

$summary['RAM'] = Get-ITGlassesMemoryStatus

Write-Host ''
Write-Host 'ITGlasses:'
foreach ($item in $summary.GetEnumerator()) { Write-Host ('  {0,-8} {1}' -f $item.Key, $item.Value) }
Write-Host "  Logs     $logs"
Write-Host '  Para apagar todo: .\scripts\stop-all.ps1'
