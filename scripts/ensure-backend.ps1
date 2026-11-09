param([ValidateSet('mock','ollama')][string]$Provider = 'ollama')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$baseUrl = 'http://127.0.0.1:8000'
$startupTimeoutSeconds = 45
$healthTimeoutSeconds = 2
$pollIntervalSeconds = 1
$script:LastHealthError = $null

function Test-BackendHealth {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/health" -UseBasicParsing -TimeoutSec $healthTimeoutSeconds
        if ($response.StatusCode -eq 200) {
            $script:LastHealthError = $null
            return $true
        }

        $script:LastHealthError = "HTTP $($response.StatusCode)"
        return $false
    } catch {
        $script:LastHealthError = $_.Exception.Message
        return $false
    }
}

function Get-BackendListeners {
    [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq 8000 }
}

function Stop-VerifiedProcess {
    param(
        [int]$ProcessId,
        [datetime]$ExpectedStartTime
    )

    $process = $null
    try {
        $process = [System.Diagnostics.Process]::GetProcessById($ProcessId)
        if ([Math]::Abs(($process.StartTime - $ExpectedStartTime).TotalSeconds) -gt 2) {
            Write-Warning "Skipped PID ${ProcessId}: its start time no longer matches the startup process."
            return
        }
        $process.Kill()
    } catch {
        Write-Warning "Could not stop verified startup PID ${ProcessId}: $($_.Exception.Message)"
    } finally {
        if ($process) {
            $process.Dispose()
        }
    }
}

function Stop-LaunchedProcessTree {
    param(
        [int]$RootProcessId,
        [datetime]$RootStartTime,
        [datetime]$StartedAt
    )

    $processJob = Start-Job -ScriptBlock { Get-CimInstance Win32_Process -ErrorAction Stop }
    try {
        if (-not (Wait-Job -Job $processJob -Timeout 5)) {
            Stop-Job -Job $processJob
            throw 'Timed out while inspecting the failed startup process tree.'
        }
        $processes = @(Receive-Job -Job $processJob -ErrorAction Stop)
    } finally {
        Remove-Job -Job $processJob -Force -ErrorAction SilentlyContinue
    }
    $knownIds = @{}
    $knownIds[$RootProcessId] = $true
    $descendantIds = New-Object System.Collections.ArrayList
    $foundDescendant = $true

    while ($foundDescendant) {
        $foundDescendant = $false
        foreach ($process in $processes) {
            $processId = [int]$process.ProcessId
            $parentProcessId = [int]$process.ParentProcessId
            if ($knownIds.ContainsKey($parentProcessId) -and -not $knownIds.ContainsKey($processId)) {
                if ($process.CreationDate -and ([datetime]$process.CreationDate) -ge $StartedAt.AddSeconds(-2)) {
                    $knownIds[$processId] = $true
                    [void]$descendantIds.Add([pscustomobject]@{
                        ProcessId = $processId
                        CreationDate = $process.CreationDate
                    })
                    $foundDescendant = $true
                }
            }
        }
    }

    for ($index = $descendantIds.Count - 1; $index -ge 0; $index--) {
        $descendant = $descendantIds[$index]
        Stop-VerifiedProcess -ProcessId ([int]$descendant.ProcessId) -ExpectedStartTime ([datetime]$descendant.CreationDate)
    }

    $rootRecord = $processes | Where-Object { [int]$_.ProcessId -eq $RootProcessId } | Select-Object -First 1
    if ($rootRecord -and $rootRecord.CreationDate) {
        $rootCreationTime = [datetime]$rootRecord.CreationDate
        if ([Math]::Abs(($rootCreationTime - $RootStartTime).TotalSeconds) -le 2) {
            Stop-VerifiedProcess -ProcessId $RootProcessId -ExpectedStartTime $RootStartTime
        }
    }
}

if (Test-BackendHealth) {
    Write-Output "ITGlasses backend is already healthy at $baseUrl; no process was started."
    exit 0
}

$listeners = @(Get-BackendListeners)
if ($listeners.Count -gt 0) {
    $addresses = ($listeners | ForEach-Object { $_.ToString() }) -join ', '
    Write-Error "Port 8000 is occupied ($addresses), but ITGlasses did not return HTTP 200 from /health. No process was started."
    exit 1
}

$startScript = Join-Path $PSScriptRoot 'start-backend.ps1'
if (-not (Test-Path -LiteralPath $startScript -PathType Leaf)) {
    Write-Error "Backend startup script was not found: $startScript"
    exit 1
}

$logDirectory = Join-Path $env:LOCALAPPDATA 'ITGlasses\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logName = 'backend-start-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $PID
$stdoutPath = Join-Path $logDirectory "$logName.out.log"
$stderrPath = Join-Path $logDirectory "$logName.err.log"
$powerShellPath = (Get-Process -Id $PID).Path
if (-not $powerShellPath) {
    $powerShellPath = Join-Path $PSHOME 'powershell.exe'
}
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Provider {1}' -f $startScript, $Provider
$startedAt = Get-Date

try {
    $launcher = Start-Process -FilePath $powerShellPath `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
} catch {
    Write-Error "Could not launch the independent backend process: $($_.Exception.Message)"
    exit 1
}
$launcherStartTime = $launcher.StartTime

if ($env:ITGLASSES_WHISPER_PYTHON -and $env:ITGLASSES_WHISPER_MODEL) {
    Write-Output 'Whisper Small environment settings will be inherited by the backend process.'
} elseif ($env:ITGLASSES_WHISPER_PYTHON -or $env:ITGLASSES_WHISPER_MODEL) {
    Write-Warning 'Only one Whisper Small environment setting is present; the backend will inherit the incomplete configuration.'
}

$startupTimer = [System.Diagnostics.Stopwatch]::StartNew()
while ($startupTimer.Elapsed.TotalSeconds -lt $startupTimeoutSeconds) {
    if (Test-BackendHealth) {
        Write-Output "ITGlasses backend is healthy at $baseUrl (startup launcher PID $($launcher.Id)); logs: $logDirectory"
        exit 0
    }

    $launcher.Refresh()
    if ($launcher.HasExited) {
        break
    }
    Start-Sleep -Seconds $pollIntervalSeconds
}

if (Test-BackendHealth) {
    Write-Output "ITGlasses backend is healthy at $baseUrl (startup launcher PID $($launcher.Id)); logs: $logDirectory"
    exit 0
}

try {
    Stop-LaunchedProcessTree -RootProcessId $launcher.Id -RootStartTime $launcherStartTime -StartedAt $startedAt
} catch {
    Write-Warning "Could not fully clean up the failed startup process tree rooted at PID $($launcher.Id): $($_.Exception.Message)"
}

Write-Output "ERROR: ITGlasses did not become healthy within $startupTimeoutSeconds seconds. Last health-check error: $script:LastHealthError. Startup logs: $stdoutPath and $stderrPath"
if (Test-Path -LiteralPath $stdoutPath) {
    Get-Content -LiteralPath $stdoutPath -Tail 30
}
if (Test-Path -LiteralPath $stderrPath) {
    Get-Content -LiteralPath $stderrPath -Tail 30
}
exit 1
