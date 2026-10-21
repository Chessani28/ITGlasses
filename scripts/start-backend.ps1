param([ValidateSet('mock','ollama')][string]$Provider = 'ollama')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$previousProvider = $env:ITGLASSES_PROVIDER
try {
    $env:ITGLASSES_PROVIDER = $Provider
    Push-Location $projectRoot
    try { & "$projectRoot\.venv\Scripts\python.exe" -m backend.run }
    finally { Pop-Location }
} finally { $env:ITGLASSES_PROVIDER = $previousProvider }
