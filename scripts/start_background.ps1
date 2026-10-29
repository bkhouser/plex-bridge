[CmdletBinding()]
param([switch]$CheckOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PaServiceState {
    param(
        [object[]]$Processes,
        [object[]]$Listeners,
        [int]$Port,
        [string[]]$Executables,
        [string]$CommandPattern = '',
        [string[]]$DescendantExecutables = @(),
        [string]$LauncherPath = ''
    )
    # Identity is executable path plus command, never a process name alone.
    $candidateIds = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($process in $Processes) {
        $matchesExecutable = $process.ExecutablePath -and $process.ExecutablePath -in $Executables
        if ($matchesExecutable -and $CommandPattern -and -not $process.CommandLine -and
            $process.ExecutablePath -eq $Executables[0]) {
            throw "The existing service command for port $Port is unreadable; refusing duplicate startup."
        }
        if ($matchesExecutable -and (-not $CommandPattern -or $process.CommandLine -match $CommandPattern)) {
            [void]$candidateIds.Add([int]$process.ProcessId)
        }
        if ($LauncherPath -and $process.Name -in @('pwsh.exe', 'powershell.exe')) {
            $launcherPattern = '(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($LauncherPath) + '"?(?:\s|$)'
            if ($process.CommandLine -match $launcherPattern) {
                [void]$candidateIds.Add([int]$process.ProcessId)
            }
        }
    }
    # A launcher and its descendants are one instance, not duplicates.
    $rootIds = @($Processes | Where-Object {
        $candidateIds.Contains([int]$_.ProcessId) -and -not $candidateIds.Contains([int]$_.ParentProcessId)
    } | ForEach-Object { [int]$_.ProcessId })
    if ($rootIds.Count -gt 1) { throw "Multiple matching instances for port $Port; refusing to change processes." }
    $familyIds = [System.Collections.Generic.HashSet[int]]::new($candidateIds)
    do {
        $added = $false
        foreach ($process in $Processes) {
            if ($familyIds.Contains([int]$process.ParentProcessId) -and
                $process.ExecutablePath -in $DescendantExecutables) {
                if ($familyIds.Add([int]$process.ProcessId)) { $added = $true }
            }
        }
    } while ($added)
    $portListeners = @($Listeners | Where-Object { $_.LocalPort -eq $Port })
    foreach ($listener in $portListeners) {
        if (-not $familyIds.Contains([int]$listener.OwningProcess)) {
            throw "Port $Port belongs to another process; refusing to start or stop anything."
        }
        if ($listener.LocalAddress -notin @('127.0.0.1', '::1')) {
            throw "Port $Port is not loopback-only; refusing startup."
        }
    }
    $state = if ($portListeners.Count) { 'listening' } elseif ($familyIds.Count) { 'starting' } else { 'missing' }
    [pscustomobject]@{ State = $state; Port = $Port; ProcessIds = @($familyIds | Sort-Object) }
}

function Get-PaTopology {
    # Permission errors terminate the operation; there is no fallback enumeration.
    [pscustomobject]@{
        Processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        Listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
    }
}

function Wait-PaHttpStatus {
    param([string]$Url, [int]$Expected, [int]$TimeoutSeconds = 60)
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(5)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    try {
        do {
            $response = $null
            try {
                $response = $client.GetAsync($Url).GetAwaiter().GetResult()
                if ([int]$response.StatusCode -eq $Expected) { return }
            } catch [System.Net.Http.HttpRequestException] {
                $socketError = $_.Exception.GetBaseException()
                if ($socketError -is [System.Net.Sockets.SocketException] -and
                    $socketError.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AccessDenied) {
                    throw 'Local socket access was denied; startup checks were stopped.'
                }
                # Startup may precede the listener; retry within the bounded deadline.
            } catch [System.Threading.Tasks.TaskCanceledException] {
                # A stalled local request has the same bounded startup allowance.
            } finally {
                if ($response) { $response.Dispose() }
            }
            Start-Sleep -Seconds 2
        } while ([DateTime]::UtcNow -lt $deadline)
        throw "Local endpoint did not reach expected HTTP $Expected within the startup deadline."
    } finally { $client.Dispose(); $handler.Dispose() }
}

# Tests dot-source only the pure helpers; no startup work is performed.
if ($MyInvocation.InvocationName -eq '.') { return }

$root = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $root 'data\startup'
$stage = 'initialization'
$mutex = $null
$locked = $false
try {
    if ($PSVersionTable.PSVersion.Major -lt 7 -or $env:OS -ne 'Windows_NT') {
        throw 'Windows PowerShell 7 is required.'
    }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $mutex = [System.Threading.Mutex]::new($false, ('Local\PlexAdvisorStartup-' + $sid))
    try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) {
        Write-Output '{"result":"another_startup_check_is_running"}'
        exit 0
    }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    $application = Join-Path $root '.venv\Scripts\plex-advisor.exe'
    $tunnel = Join-Path $root 'data\tunnel-client\v0.0.16\tunnel-client.exe'
    $profilePath = Join-Path $root 'data\tunnel-client\profiles\plex-advisor.yaml'
    $launcher = Join-Path $root 'scripts\start_tunnel.ps1'
    $shell = Join-Path $PSHOME 'pwsh.exe'
    foreach ($path in @($python, $application, $tunnel, $profilePath, $launcher, $shell)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A required startup file is missing.' }
    }
    Push-Location -LiteralPath $root
    try {
        $stage = 'configuration'
        $configurationCode = @'
import json,sys
from plex_advisor.config import Settings
try:
 s=Settings();s.require_auth()
 assert s.auth_mode=='oauth' and s.mcp_host=='127.0.0.1' and s.mcp_port==8767
 print(json.dumps({'base_python':sys._base_executable}))
except Exception:
 sys.exit(2)
'@
        $configurationJson = & $python -B -c $configurationCode 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Expected OAuth/loopback/8767 configuration is unavailable.' }
        $configuration = $configurationJson | ConvertFrom-Json
        $profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
        if (@($profile.mcp.server_urls).Count -ne 1 -or
            $profile.mcp.server_urls[0].channel -ne 'main' -or
            $profile.mcp.server_urls[0].url -ne 'http://127.0.0.1:8767/mcp' -or
            $profile.health.listen_addr -ne '127.0.0.1:8766') {
            throw 'The tunnel profile does not match the approved local ports.'
        }
        $appArguments = @{
            Port = 8767
            Executables = @($application, $python, $configuration.base_python)
            CommandPattern = '(?i)plex[-_]advisor.*(?:^|\s)serve(?:\s|$)'
            DescendantExecutables = @($python, $configuration.base_python)
        }
        $tunnelArguments = @{
            Port = 8766; Executables = @($tunnel); LauncherPath = $launcher
            DescendantExecutables = @($tunnel)
        }
        $stage = 'ownership_checks'
        $topology = Get-PaTopology
        $appState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @appArguments
        $tunnelState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @tunnelArguments
        $appStarted = $false
        $tunnelStarted = $false
        if ($CheckOnly -and ($appState.State -eq 'missing' -or $tunnelState.State -eq 'missing')) {
            throw 'Check-only mode found a missing service; nothing was started.'
        }
        if ($appState.State -eq 'missing') {
            $stage = 'application_start'
            # Recheck immediately before starting, including both port owners.
            $topology = Get-PaTopology
            $appState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @appArguments
            if ($appState.State -eq 'missing') {
                [void][System.IO.Directory]::CreateDirectory($logDirectory)
                $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
                $null = Start-Process -FilePath $application -ArgumentList @('serve') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "application-$stamp.stdout.log") -RedirectStandardError (Join-Path $logDirectory "application-$stamp.stderr.log") -PassThru
                $appStarted = $true
            }
        }
        $stage = 'oauth_preflight'
        Wait-PaHttpStatus -Url 'http://127.0.0.1:8767/mcp' -Expected 401
        $null = & $python -B (Join-Path $PSScriptRoot 'check_tunnel_auth.py') $profilePath 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'OAuth preflight failed; tunnel startup was refused.' }
        # Verify ownership again after waiting, before any tunnel startup.
        $topology = Get-PaTopology
        $appState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @appArguments
        if ($appState.State -ne 'listening') { throw 'Application listener ownership could not be verified.' }
        $tunnelState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @tunnelArguments
        if ($tunnelState.State -eq 'missing' -and -not $CheckOnly) {
            $stage = 'tunnel_start'
            [void][System.IO.Directory]::CreateDirectory($logDirectory)
            $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
            $null = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-NonInteractive', '-File', ('"' + $launcher + '"')) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "tunnel-$stamp.stdout.log") -RedirectStandardError (Join-Path $logDirectory "tunnel-$stamp.stderr.log") -PassThru
            $tunnelStarted = $true
        }
        $stage = 'readiness'
        Wait-PaHttpStatus -Url 'http://127.0.0.1:8766/readyz' -Expected 200
        $topology = Get-PaTopology
        $appState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @appArguments
        $tunnelState = Get-PaServiceState -Processes $topology.Processes -Listeners $topology.Listeners @tunnelArguments
        if ($appState.State -ne 'listening' -or $tunnelState.State -ne 'listening') {
            throw 'Final listener ownership could not be verified.'
        }
        $result = [ordered]@{
            time_utc = [DateTime]::UtcNow.ToString('o'); result = 'healthy'
            application_started = $appStarted; tunnel_started = $tunnelStarted
            application_pids = $appState.ProcessIds; tunnel_pids = $tunnelState.ProcessIds
            anonymous_http = 401; tunnel_ready_http = 200; oauth_preflight = 'passed'
        } | ConvertTo-Json -Compress
        if (-not $CheckOnly) {
            [void][System.IO.Directory]::CreateDirectory($logDirectory)
            Add-Content -LiteralPath (Join-Path $logDirectory 'startup.log') -Value $result -Encoding utf8
        }
        Write-Output $result
    } finally { Pop-Location }
} catch {
    $failure = [ordered]@{
        time_utc = [DateTime]::UtcNow.ToString('o'); result = 'failed_safely'; stage = $stage
        error_type = $_.Exception.GetType().Name; reason = $_.Exception.Message
    } | ConvertTo-Json -Compress
    if (-not $CheckOnly -and (Test-Path -LiteralPath $logDirectory)) {
        Add-Content -LiteralPath (Join-Path $logDirectory 'startup.log') -Value $failure -Encoding utf8
    }
    Write-Output $failure
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
