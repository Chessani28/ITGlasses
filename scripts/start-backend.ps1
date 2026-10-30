param([ValidateSet('mock','ollama')][string]$Provider = 'ollama')
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$baseUrl = "http://127.0.0.1:8000"

function Test-BackendHealth {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-BackendHealth) {
    Write-Host "ITGlasses backend already running and healthy on $baseUrl"
    exit 0
}

$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    Write-Host "Port 8000 is already in use by PID(s): $($pids -join ', ')"
    Write-Host "No healthy ITGlasses instance was detected. Refusing to start a duplicate or switch ports."
    Write-Host "Inspect the owning process and free port 8000 before retrying."
    exit 1
}

$previousProvider = $env:ITGLASSES_PROVIDER
try {
    $env:ITGLASSES_PROVIDER = $Provider
    Push-Location $projectRoot
    try { & "$projectRoot\.venv\Scripts\python.exe" -m backend.run }
    finally { Pop-Location }
} finally { $env:ITGLASSES_PROVIDER = $previousProvider }
