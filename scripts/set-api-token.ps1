[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$secretDirectory = Join-Path $projectRoot '.runtime'
$secretPath = Join-Path $secretDirectory 'api-token.dpapi'

if ((Test-Path -LiteralPath $secretPath -PathType Leaf) -and -not $Replace) {
    throw 'A protected API token already exists. Use -Replace to explicitly rotate it.'
}
if ((Test-Path -LiteralPath $secretPath -PathType Leaf) -and
    -not $PSCmdlet.ShouldProcess('the local protected API token', 'Replace')) {
    return
}
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf) -and
    -not $PSCmdlet.ShouldProcess('the local protected API token', 'Create')) {
    return
}

$secureToken = Read-Host 'Enter the API bearer token' -AsSecureString
if (-not $secureToken -or $secureToken.Length -eq 0) {
    throw 'API bearer token cannot be empty.'
}
$credential = New-Object System.Management.Automation.PSCredential('ITGlasses', $secureToken)
$plainToken = $credential.GetNetworkCredential().Password
if ($plainToken.Contains(',')) {
    throw 'API bearer token cannot contain a comma.'
}
$encrypted = ConvertFrom-SecureString -SecureString $secureToken

New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
$writer = $null
if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
    [IO.File]::WriteAllText($secretPath, $encrypted)
} else {
    $stream = [IO.File]::Open(
        $secretPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $writer = New-Object IO.StreamWriter($stream)
        $writer.Write($encrypted)
        $writer.Flush()
    } finally {
        if ($writer) { $writer.Dispose() } else { $stream.Dispose() }
    }
}
Write-Output 'Protected API token saved for the current Windows user.'
