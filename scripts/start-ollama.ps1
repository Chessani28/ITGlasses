$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$binary = Join-Path $projectRoot 'storage\runtime\ollama-0.40.6\ollama.exe'
if (!(Test-Path -LiteralPath $binary)) { throw 'Verified Ollama runtime is missing.' }
$temporaryPath = Join-Path $projectRoot '.cache\tmp'
$settings = @{
    OLLAMA_HOST = '127.0.0.1:11434'
    OLLAMA_MODELS = (Join-Path $projectRoot 'storage\models')
    OLLAMA_NO_CLOUD = '1'
    OLLAMA_CONTEXT_LENGTH = '2048'
    OLLAMA_NUM_PARALLEL = '1'
    OLLAMA_MAX_LOADED_MODELS = '1'
    OLLAMA_MAX_QUEUE = '1'
    OLLAMA_KEEP_ALIVE = '30s'
    OLLAMA_LOAD_TIMEOUT = '120s'
    OLLAMA_DEBUG = '0'
    OLLAMA_DEBUG_LOG_REQUESTS = '0'
    OLLAMA_NOHISTORY = '1'
    OLLAMA_NOPRUNE = '1'
    CUDA_CACHE_PATH = (Join-Path $projectRoot '.cache\cuda')
    TEMP = $temporaryPath
    TMP = $temporaryPath
}
$previous = @{}
try {
    foreach ($key in $settings.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
    }
    New-Item -ItemType Directory -Force $temporaryPath,$settings.CUDA_CACHE_PATH,$settings.OLLAMA_MODELS | Out-Null
    & $binary serve
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
