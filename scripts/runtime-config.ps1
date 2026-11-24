$ErrorActionPreference = 'Stop'

# CTranslate2 Whisper folder: model.bin, config.json and tokenizer.json, plus vocabulary.txt (Small) or
# vocabulary.json with preprocessor_config.json (large-v3 / turbo, 128 mel bands).
function Test-ITGlassesWhisperModelDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    foreach ($fileName in @('model.bin', 'config.json', 'tokenizer.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $fileName) -PathType Leaf)) {
            return $false
        }
    }
    if (Test-Path -LiteralPath (Join-Path $Path 'vocabulary.txt') -PathType Leaf) {
        return $true
    }
    return (Test-Path -LiteralPath (Join-Path $Path 'vocabulary.json') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'preprocessor_config.json') -PathType Leaf)
}

function Set-ITGlassesRuntimeConfiguration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $names = @(
        'ITGLASSES_WHISPER_PYTHON',
        'ITGLASSES_WHISPER_MODEL',
        'ITGLASSES_REQUIRE_AUTH',
        'ITGLASSES_ALLOW_LAN_BINDING',
        'ITGLASSES_API_TOKENS',
        'ITGLASSES_PAIRING_ORIGIN',
        'ITGLASSES_WHISPER_KEEP_RAM_GIB',
        'ITGLASSES_WHISPER_HINT_MODE',
        'ITGLASSES_STT_CORRECTION',
        'ITGLASSES_WHISPER_DEVICE',
        'ITGLASSES_WHISPER_COMPUTE_TYPE',
        'ITGLASSES_WHISPER_FALLBACK_MODEL',
        'ITGLASSES_WHISPER_PRELOAD',
        'ITGLASSES_WHISPER_MIN_VRAM_MIB',
        'ITGLASSES_WHISPER_IDLE_SECONDS',
        'ITGLASSES_PASSWORD_MIN_LENGTH',
        'ITGLASSES_LOOPBACK_MODE',
        'ITGLASSES_BEARER_SCOPE'
    )
    $previous = @{}
    foreach ($name in $names) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }

    try {
        $configPath = Join-Path $ProjectRoot 'runtime-config.psd1'
        $config = @{}
        if (Test-Path -LiteralPath $configPath -PathType Leaf) {
            try {
                $config = Import-PowerShellDataFile -LiteralPath $configPath
            } catch {
                throw 'Runtime configuration is invalid. Review runtime-config.psd1.'
            }
            if ($config -isnot [System.Collections.IDictionary]) {
                throw 'Runtime configuration must be a PowerShell data hashtable.'
            }

            $allowedKeys = @(
                'WhisperPython', 'WhisperModel', 'RequireAuth', 'AllowLanBinding', 'PairingOrigin',
                'WhisperKeepRamGiB', 'WhisperHintMode', 'SttCorrection',
                'WhisperDevice', 'WhisperComputeType', 'WhisperFallbackModel', 'WhisperPreload',
                'WhisperMinVramMiB', 'WhisperIdleSeconds', 'PasswordMinLength', 'LoopbackMode', 'BearerScope'
            )
            $unknownKeys = @($config.Keys | Where-Object { $_ -notin $allowedKeys })
            if ($unknownKeys.Count -gt 0) {
                throw 'Runtime configuration contains unsupported keys.'
            }
        }

        foreach ($setting in @(
            @{ Key = 'WhisperPython'; Variable = 'ITGLASSES_WHISPER_PYTHON' },
            @{ Key = 'WhisperModel'; Variable = 'ITGLASSES_WHISPER_MODEL' }
        )) {
            $environmentValue = [Environment]::GetEnvironmentVariable($setting.Variable, 'Process')
            if ([string]::IsNullOrWhiteSpace($environmentValue) -and $config.Contains($setting.Key)) {
                if ($config[$setting.Key] -isnot [string]) {
                    throw "Runtime configuration value $($setting.Key) must be a string."
                }
                if (-not [string]::IsNullOrWhiteSpace($config[$setting.Key])) {
                    [Environment]::SetEnvironmentVariable(
                        $setting.Variable, $config[$setting.Key], 'Process'
                    )
                }
            }
        }

        foreach ($setting in @(
            @{ Key = 'RequireAuth'; Variable = 'ITGLASSES_REQUIRE_AUTH' },
            @{ Key = 'AllowLanBinding'; Variable = 'ITGLASSES_ALLOW_LAN_BINDING' }
        )) {
            $environmentValue = [Environment]::GetEnvironmentVariable($setting.Variable, 'Process')
            if ([string]::IsNullOrWhiteSpace($environmentValue) -and $config.Contains($setting.Key)) {
                if ($config[$setting.Key] -isnot [bool]) {
                    throw "Runtime configuration value $($setting.Key) must be true or false."
                }
                [Environment]::SetEnvironmentVariable(
                    $setting.Variable, [string]$config[$setting.Key], 'Process'
                )
            }
        }

        # Pairing origin for the phones behind Caddy. Must be identical to the APK's ITGLASSES_PAIRING_ORIGIN.
        $originValue = [Environment]::GetEnvironmentVariable('ITGLASSES_PAIRING_ORIGIN', 'Process')
        if ([string]::IsNullOrWhiteSpace($originValue) -and $config.Contains('PairingOrigin')) {
            if ($config.PairingOrigin -isnot [string]) {
                throw 'Runtime configuration value PairingOrigin must be a string.'
            }
            $originValue = $config.PairingOrigin
        }
        if (-not [string]::IsNullOrWhiteSpace($originValue)) {
            if ($originValue -cnotmatch '^https://[a-z0-9.-]+(:[0-9]{1,5})?$' -or $originValue -match ':443$') {
                throw 'Pairing origin must be a canonical HTTPS origin such as https://192.168.1.50:8443.'
            }
            [Environment]::SetEnvironmentVariable('ITGLASSES_PAIRING_ORIGIN', $originValue, 'Process')
        }

        # Access modes (stage usuarios 4): what this PC may do without a login, and what shared tokens reach.
        foreach ($setting in @(
            @{ Key = 'LoopbackMode'; Variable = 'ITGLASSES_LOOPBACK_MODE'; Allowed = @('legacy', 'chat', 'off') },
            @{ Key = 'BearerScope'; Variable = 'ITGLASSES_BEARER_SCOPE'; Allowed = @('mobile', 'full') }
        )) {
            $environmentValue = [Environment]::GetEnvironmentVariable($setting.Variable, 'Process')
            if ([string]::IsNullOrWhiteSpace($environmentValue) -and $config.Contains($setting.Key)) {
                $choice = $config[$setting.Key]
                if ($choice -isnot [string] -or $choice -cnotin $setting.Allowed) {
                    throw "Runtime configuration value $($setting.Key) must be one of: $($setting.Allowed -join ', ')."
                }
                [Environment]::SetEnvironmentVariable($setting.Variable, $choice, 'Process')
            }
        }

        # Free RAM (GiB) needed to keep Whisper loaded between voice questions. Optional: the backend uses 8 GiB
        # by default and never less than its 3 GiB load limit.
        # Voz 2: Whisper vocabulary hint (hotwords, initial_prompt or off) and the transcript correction (on/off).
        # Optional: the backend uses hotwords and the correction on by default.
        $hintValue = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_HINT_MODE', 'Process')
        if ([string]::IsNullOrWhiteSpace($hintValue) -and $config.Contains('WhisperHintMode')) {
            if ($config.WhisperHintMode -isnot [string] -or
                $config.WhisperHintMode -cnotin @('hotwords', 'initial_prompt', 'off')) {
                throw 'Runtime configuration value WhisperHintMode must be hotwords, initial_prompt or off.'
            }
            [Environment]::SetEnvironmentVariable('ITGLASSES_WHISPER_HINT_MODE', $config.WhisperHintMode, 'Process')
        }
        $correctionValue = [Environment]::GetEnvironmentVariable('ITGLASSES_STT_CORRECTION', 'Process')
        if ([string]::IsNullOrWhiteSpace($correctionValue) -and $config.Contains('SttCorrection')) {
            if ($config.SttCorrection -isnot [bool]) {
                throw 'Runtime configuration value SttCorrection must be true or false.'
            }
            [Environment]::SetEnvironmentVariable(
                'ITGLASSES_STT_CORRECTION', ([string]$config.SttCorrection).ToLowerInvariant(), 'Process'
            )
        }

        $keepValue = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_KEEP_RAM_GIB', 'Process')
        if ([string]::IsNullOrWhiteSpace($keepValue) -and $config.Contains('WhisperKeepRamGiB')) {
            $keep = $config.WhisperKeepRamGiB
            if (($keep -isnot [int] -and $keep -isnot [long] -and $keep -isnot [double] -and $keep -isnot [decimal]) -or
                $keep -le 0) {
                throw 'Runtime configuration value WhisperKeepRamGiB must be a positive number of GiB.'
            }
            [Environment]::SetEnvironmentVariable(
                'ITGLASSES_WHISPER_KEEP_RAM_GIB',
                ([double]$keep).ToString([Globalization.CultureInfo]::InvariantCulture),
                'Process'
            )
        }

        # Voz 3: Whisper on the GPU. Optional: the backend defaults to cpu with int8, no fallback, no preload,
        # 1000 MiB of free VRAM to load and 900 s of inactivity on the GPU. Environment values win.
        $deviceValue = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_DEVICE', 'Process')
        if ([string]::IsNullOrWhiteSpace($deviceValue) -and $config.Contains('WhisperDevice')) {
            if ($config.WhisperDevice -isnot [string] -or $config.WhisperDevice -cnotin @('cpu', 'cuda')) {
                throw 'Runtime configuration value WhisperDevice must be cpu or cuda.'
            }
            $deviceValue = $config.WhisperDevice
            [Environment]::SetEnvironmentVariable('ITGLASSES_WHISPER_DEVICE', $deviceValue, 'Process')
        }
        $device = 'cpu'
        if (-not [string]::IsNullOrWhiteSpace($deviceValue)) {
            $device = $deviceValue.Trim().ToLowerInvariant()
        }
        $computeValue = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_COMPUTE_TYPE', 'Process')
        if ([string]::IsNullOrWhiteSpace($computeValue) -and $config.Contains('WhisperComputeType')) {
            $allowedCompute = @('int8', 'int8_float32', 'float32')
            if ($device -eq 'cuda') {
                $allowedCompute = @('int8_float16', 'float16', 'int8')
            }
            if ($config.WhisperComputeType -isnot [string] -or $config.WhisperComputeType -cnotin $allowedCompute) {
                throw "Runtime configuration value WhisperComputeType is not valid for the $device device."
            }
            [Environment]::SetEnvironmentVariable('ITGLASSES_WHISPER_COMPUTE_TYPE', $config.WhisperComputeType, 'Process')
        }
        foreach ($setting in @(
            @{ Key = 'WhisperMinVramMiB'; Variable = 'ITGLASSES_WHISPER_MIN_VRAM_MIB'; Minimum = 0 },
            @{ Key = 'WhisperIdleSeconds'; Variable = 'ITGLASSES_WHISPER_IDLE_SECONDS'; Minimum = 30 },
            # Web password minimum (5 to 256; the backend refuses to start above 256).
            @{ Key = 'PasswordMinLength'; Variable = 'ITGLASSES_PASSWORD_MIN_LENGTH'; Minimum = 5 }
        )) {
            $environmentValue = [Environment]::GetEnvironmentVariable($setting.Variable, 'Process')
            if ([string]::IsNullOrWhiteSpace($environmentValue) -and $config.Contains($setting.Key)) {
                $number = $config[$setting.Key]
                if (($number -isnot [int] -and $number -isnot [long]) -or $number -lt $setting.Minimum) {
                    throw "Runtime configuration value $($setting.Key) must be a whole number of at least $($setting.Minimum)."
                }
                [Environment]::SetEnvironmentVariable(
                    $setting.Variable, ([long]$number).ToString([Globalization.CultureInfo]::InvariantCulture), 'Process'
                )
            }
        }
        $preloadValue = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_PRELOAD', 'Process')
        if ([string]::IsNullOrWhiteSpace($preloadValue) -and $config.Contains('WhisperPreload')) {
            if ($config.WhisperPreload -isnot [bool]) {
                throw 'Runtime configuration value WhisperPreload must be true or false.'
            }
            [Environment]::SetEnvironmentVariable(
                'ITGLASSES_WHISPER_PRELOAD', ([string]$config.WhisperPreload).ToLowerInvariant(), 'Process'
            )
        }
        $fallbackPath = [Environment]::GetEnvironmentVariable('ITGLASSES_WHISPER_FALLBACK_MODEL', 'Process')
        if ([string]::IsNullOrWhiteSpace($fallbackPath) -and $config.Contains('WhisperFallbackModel')) {
            if ($config.WhisperFallbackModel -isnot [string]) {
                throw 'Runtime configuration value WhisperFallbackModel must be a string.'
            }
            $fallbackPath = $config.WhisperFallbackModel
            if (-not [string]::IsNullOrWhiteSpace($fallbackPath)) {
                [Environment]::SetEnvironmentVariable('ITGLASSES_WHISPER_FALLBACK_MODEL', $fallbackPath, 'Process')
            }
        }
        if (-not [string]::IsNullOrWhiteSpace($fallbackPath)) {
            if (-not [IO.Path]::IsPathRooted($fallbackPath) -or
                -not (Test-Path -LiteralPath $fallbackPath -PathType Container) -or
                -not (Test-ITGlassesWhisperModelDirectory -Path $fallbackPath)) {
                throw 'Whisper fallback model directory is invalid or incomplete.'
            }
        }

        $pythonPath = [Environment]::GetEnvironmentVariable(
            'ITGLASSES_WHISPER_PYTHON', 'Process'
        )
        $modelPath = [Environment]::GetEnvironmentVariable(
            'ITGLASSES_WHISPER_MODEL', 'Process'
        )
        $pythonConfigured = -not [string]::IsNullOrWhiteSpace($pythonPath)
        $modelConfigured = -not [string]::IsNullOrWhiteSpace($modelPath)
        if ($pythonConfigured -xor $modelConfigured) {
            throw 'Whisper Small configuration is incomplete. Set both Whisper paths or neither.'
        }
        if ($pythonConfigured) {
            if (-not [IO.Path]::IsPathRooted($pythonPath) -or
                [IO.Path]::GetExtension($pythonPath) -ine '.exe' -or
                -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
                throw 'Whisper Small Python executable path is invalid.'
            }
            if (-not [IO.Path]::IsPathRooted($modelPath) -or
                -not (Test-Path -LiteralPath $modelPath -PathType Container)) {
                throw 'Whisper Small model directory path is invalid.'
            }
            if (-not (Test-ITGlassesWhisperModelDirectory -Path $modelPath)) {
                throw 'Whisper Small model directory is incomplete.'
            }
        }

        $hostValue = [Environment]::GetEnvironmentVariable('ITGLASSES_HOST', 'Process')
        $lanValue = [Environment]::GetEnvironmentVariable(
            'ITGLASSES_ALLOW_LAN_BINDING', 'Process'
        )
        $configToml = Join-Path $ProjectRoot 'config.toml'
        if (Test-Path -LiteralPath $configToml -PathType Leaf) {
            foreach ($line in Get-Content -LiteralPath $configToml) {
                if ($line -match '^\s*host\s*=\s*["'']([^"'']+)["'']\s*(?:#.*)?$' -and
                    [string]::IsNullOrWhiteSpace($hostValue)) {
                    $hostValue = $Matches[1]
                }
                if ($line -match '^\s*allow_lan_binding\s*=\s*(true|false)\s*(?:#.*)?$' -and
                    [string]::IsNullOrWhiteSpace($lanValue)) {
                    $lanValue = $Matches[1]
                }
            }
        }

        $requireAuthValue = [Environment]::GetEnvironmentVariable(
            'ITGLASSES_REQUIRE_AUTH', 'Process'
        )
        if ([string]::IsNullOrWhiteSpace($requireAuthValue) -and $config.Contains('RequireAuth')) {
            $requireAuthValue = [string]$config.RequireAuth
        }
        $authRequired = (
            $requireAuthValue -match '^(?i:true|1|yes)$'
        ) -or (
            $lanValue -match '^(?i:true|1|yes)$'
        ) -or (
            -not [string]::IsNullOrWhiteSpace($hostValue) -and $hostValue -ne '127.0.0.1'
        )

        $token = [Environment]::GetEnvironmentVariable('ITGLASSES_API_TOKENS', 'Process')
        if ([string]::IsNullOrWhiteSpace($token) -and $authRequired) {
            $secretPath = Join-Path $ProjectRoot '.runtime\api-token.dpapi'
            if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
                try {
                    $encrypted = [IO.File]::ReadAllText($secretPath).Trim()
                    $secureToken = ConvertTo-SecureString -String $encrypted
                    $credential = New-Object System.Management.Automation.PSCredential(
                        'ITGlasses', $secureToken
                    )
                    $token = $credential.GetNetworkCredential().Password
                } catch {
                    throw 'Protected API token could not be read for this Windows user.'
                }
                if ([string]::IsNullOrWhiteSpace($token) -or $token.Contains(',')) {
                    throw 'Protected API token is invalid.'
                }
                [Environment]::SetEnvironmentVariable(
                    'ITGLASSES_API_TOKENS', $token, 'Process'
                )
            }
        }
        if ($authRequired -and [string]::IsNullOrWhiteSpace($token)) {
            throw 'API authentication is required but no operator token is configured. Set ITGLASSES_API_TOKENS or provision the protected local token.'
        }

        return $previous
    } catch {
        Restore-ITGlassesRuntimeEnvironment -Previous $previous
        throw
    }
}

function Restore-ITGlassesRuntimeEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$Previous
    )

    foreach ($name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Previous[$name], 'Process')
    }
}
