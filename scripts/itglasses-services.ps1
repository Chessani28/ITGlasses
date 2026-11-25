# Helpers for start-all.ps1 and stop-all.ps1. Functions only: dot-source this file.

$script:ITGlassesEmbeddingModel = 'qwen3-embedding:0.6b'
$script:ITGlassesOllamaUrl = 'http://127.0.0.1:11434'

function Get-ITGlassesCaddyBind {
    # The LAN address Caddy listens on ("bind" in the Caddyfile), or $null.
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*bind\s+(\S+)') { return $Matches[1] }
    }
    return $null
}

function Get-ITGlassesMissingModels {
    # The required Ollama models that are not in the available list.
    param([string[]]$Available, [string[]]$Required)
    $Required | Where-Object { $_ -and ($_ -notin $Available) }
}

function Get-ITGlassesOllamaModels {
    # Model names served by the running Ollama, or $null when nothing answers.
    try {
        $tags = Invoke-RestMethod -Uri "$script:ITGlassesOllamaUrl/api/tags" -TimeoutSec 3
        return @($tags.models | ForEach-Object { $_.name })
    } catch {
        return $null
    }
}

function Get-ITGlassesLanAddresses {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
        ForEach-Object { $_.IPAddress }
}

function Test-ITGlassesPort {
    param([int]$Port)
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Get-ITGlassesChatModel {
    # The chat model the backend is configured to use (config.toml / Settings).
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    (& $python -c "from backend.app.core.config import settings; print(settings.model)").Trim()
}
