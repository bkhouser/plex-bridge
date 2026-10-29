$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This private key helper requires Windows.' }

$taskRoot = Split-Path -Parent $PSScriptRoot
$taskDirectory = Join-Path $taskRoot 'data\tunnel-client'
$taskKeyPath = Join-Path $taskDirectory 'runtime-key.dpapi'
$taskSecret = Read-Host 'Paste the OpenAI tunnel RUNTIME API key (input is hidden)' -AsSecureString
try {
    if ($taskSecret.Length -lt 20) { throw 'No usable runtime key was entered.' }
    New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
    $taskCiphertext = ConvertFrom-SecureString -SecureString $taskSecret
    [System.IO.File]::WriteAllText($taskKeyPath, $taskCiphertext)
    Write-Output 'Runtime key saved encrypted for this Windows account. The key was not printed.'
} finally {
    $taskSecret.Dispose()
}
