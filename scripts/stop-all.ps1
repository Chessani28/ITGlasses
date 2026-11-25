# Apaga ITGlasses completo: Caddy, el backend y el Ollama del proyecto (solo procesos de esta instalacion;
# nunca la app de escritorio de Ollama u otros programas).
#   .\scripts\stop-all.ps1
param([string]$CaddyRoot = 'C:\ITGlasses-Caddy')

$projectRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$runtime = Join-Path $projectRoot 'storage\runtime'

function Stop-ITGlassesProcesses {
    param([string]$Label, [scriptblock]$Match)
    $found = @(Get-CimInstance Win32_Process | Where-Object $Match)
    foreach ($process in $found) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host ('{0,-8} {1}' -f $Label, $(if ($found.Count) { "detenido ($($found.Count) proceso(s))" } else { 'no corria' }))
}

Stop-ITGlassesProcesses 'Caddy' { $_.Name -eq 'caddy.exe' -and $_.ExecutablePath -like "$CaddyRoot\*" }
& (Join-Path $PSScriptRoot 'stop-backend.ps1')
Stop-ITGlassesProcesses 'Ollama' {
    $_.ExecutablePath -like "$runtime\*" -and ($_.Name -eq 'ollama.exe' -or $_.Name -eq 'llama-server.exe')
}
