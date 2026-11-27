# Prueba de carga: muchos técnicos preguntando a la vez contra el modelo real (Ollama debe estar encendido).
# Usa una copia aislada en el puerto 8010 con usuarios e historial de prueba en .cache\load\<hora>\; nunca toca
# storage\, el puerto 8000 ni Caddy. El reporte queda en esa carpeta (report.md y report.json).
# Ejemplos:
#   .\scripts\run-load-test.ps1
#   .\scripts\run-load-test.ps1 --levels 1,5,10 --turns 3
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:TEMP = Join-Path $root '.cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
& (Join-Path $root '.venv\Scripts\python.exe') -m evals.load_test @args
exit $LASTEXITCODE
