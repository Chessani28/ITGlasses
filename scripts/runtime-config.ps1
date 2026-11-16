$ErrorActionPreference = 'Stop'

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
        'ITGLASSES_API_TOKENS'
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
                'WhisperPython', 'WhisperModel', 'RequireAuth', 'AllowLanBinding'
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
            foreach ($fileName in @('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')) {
                if (-not (Test-Path -LiteralPath (Join-Path $modelPath $fileName) -PathType Leaf)) {
                    throw 'Whisper Small model directory is incomplete.'
                }
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
