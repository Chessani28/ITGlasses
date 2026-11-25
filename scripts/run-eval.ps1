# Examen del cerebro de ITGlasses. Corre en proceso (sin servidor ni puerto 8000) y escribe el reporte en
# evals\results\. Con -Provider ollama (por defecto) usa el modelo real y una copia del índice de manuales.
# Ejemplos:
#   .\scripts\run-eval.ps1 --provider mock
#   .\scripts\run-eval.ps1 --category conceptos
#   .\scripts\run-eval.ps1 --compare evals\results\baseline-20261125-120000.json
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:TEMP = Join-Path $root '.cache\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
& (Join-Path $root '.venv\Scripts\python.exe') -m evals.run_eval @args
exit $LASTEXITCODE
