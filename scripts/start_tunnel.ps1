param([switch]$Doctor)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This encrypted-key launcher requires Windows.' }

$taskRoot = Split-Path -Parent $PSScriptRoot
$taskBinary = Join-Path $taskRoot 'data\tunnel-client\v0.0.16\tunnel-client.exe'
$taskProfile = Join-Path $taskRoot 'data\tunnel-client\profiles\plex-advisor.yaml'
$taskKeyPath = Join-Path $taskRoot 'data\tunnel-client\runtime-key.dpapi'
foreach ($taskPath in @($taskBinary, $taskProfile, $taskKeyPath)) {
    if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) {
        throw 'Tunnel setup is incomplete. Run scripts\configure_tunnel_key.ps1 after the client profile is prepared.'
    }
}

# The tunnel is transport only. Never start it against the local bearer mode.
Push-Location -LiteralPath $taskRoot
try {
    & (Join-Path $taskRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'check_tunnel_auth.py') $taskProfile
    if ($LASTEXITCODE -ne 0) {
        throw 'Configure and verify OAuth sign-in before starting the tunnel. Local Plex Advisor remains available.'
    }

    $taskPriorKey = [Environment]::GetEnvironmentVariable('PA_TUNNEL_API_KEY', 'Process')
    try {
        $taskSecure = ConvertTo-SecureString -String ([System.IO.File]::ReadAllText($taskKeyPath))
        $taskPointer = [IntPtr]::Zero
        try {
            $taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecure)
            [Environment]::SetEnvironmentVariable('PA_TUNNEL_API_KEY', [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer), 'Process')
        } finally {
            if ($taskPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer) }
            $taskSecure.Dispose()
        }
        if ($Doctor) {
            & $taskBinary doctor --profile-file $taskProfile --explain
        } else {
            & $taskBinary run --profile-file $taskProfile
        }
        $taskExitCode = $LASTEXITCODE
    } finally {
        [Environment]::SetEnvironmentVariable('PA_TUNNEL_API_KEY', $taskPriorKey, 'Process')
    }
    if ($taskExitCode -ne 0) { throw 'Tunnel client reported a failure; inspect its local diagnostics.' }
} finally {
    Pop-Location
}
