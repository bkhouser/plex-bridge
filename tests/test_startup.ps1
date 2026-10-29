param([string]$StartupScript = (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\start_background.ps1'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. $StartupScript

function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { throw "Failed: $Case ($Actual versus $Expected)" }
}
function Assert-Refuses([scriptblock]$Action, [string]$Case) {
    $refused = $false
    try { & $Action | Out-Null } catch { $refused = $true }
    if (-not $refused) { throw "Failed to refuse: $Case" }
}
function New-TestProcess([int]$Id, [int]$Parent, [string]$Path, [string]$Command, [string]$Name = 'python.exe') {
    [pscustomobject]@{ProcessId=$Id;ParentProcessId=$Parent;ExecutablePath=$Path;CommandLine=$Command;Name=$Name}
}
function New-TestListener([int]$Port, [int]$Owner, [string]$Address = '127.0.0.1') {
    [pscustomobject]@{LocalPort=$Port;OwningProcess=$Owner;LocalAddress=$Address}
}
$app = 'C:\test\PlexAdvisor\.venv\Scripts\plex-advisor.exe'
$venv = 'C:\test\PlexAdvisor\.venv\Scripts\python.exe'
$base = 'C:\Python312\python.exe'
$arguments = @{Port=8767;Executables=@($app,$venv,$base);CommandPattern='plex[-_]advisor.*\bserve\b';DescendantExecutables=@($venv,$base)}
$processes = @(
    (New-TestProcess 10 1 $app ('"'+$app+'" serve') 'plex-advisor.exe'),
    (New-TestProcess 11 10 $venv ('"'+$venv+'" "'+$app+'" serve')),
    (New-TestProcess 12 11 $base ('"'+$base+'" "'+$app+'" serve')),
    (New-TestProcess 99 1 'C:\Python314\pythonw.exe' 'pythonw -m center.server --no-browser' 'pythonw.exe')
)
Assert-Equal (Get-PaServiceState -Processes @() -Listeners @() @arguments).State 'missing' 'missing service'
Assert-Equal (Get-PaServiceState -Processes $processes -Listeners @() @arguments).State 'starting' 'existing process without listener is reused'
Assert-Equal (Get-PaServiceState -Processes $processes -Listeners @((New-TestListener 8767 12),(New-TestListener 8765 99)) @arguments).State 'listening' 'venv wrapper chain and unrelated center preserved'
Assert-Refuses { Get-PaServiceState -Processes $processes -Listeners @((New-TestListener 8767 99)) @arguments } 'foreign port owner'
Assert-Refuses { Get-PaServiceState -Processes $processes -Listeners @((New-TestListener 8767 12 '0.0.0.0')) @arguments } 'non-loopback binding'
$duplicate = @($processes) + @((New-TestProcess 20 1 $app ('"'+$app+'" serve') 'plex-advisor.exe'))
Assert-Refuses { Get-PaServiceState -Processes $duplicate -Listeners @() @arguments } 'duplicate independent services'
$spoof = @((New-TestProcess 30 1 'C:\other\plex-advisor.exe' 'plex-advisor.exe serve' 'plex-advisor.exe'))
Assert-Refuses { Get-PaServiceState -Processes $spoof -Listeners @((New-TestListener 8767 30)) @arguments } 'same executable name with another path'
$unreadable = @((New-TestProcess 31 1 $app '' 'plex-advisor.exe'))
Assert-Refuses { Get-PaServiceState -Processes $unreadable -Listeners @() @arguments } 'unreadable existing service command'
$tunnel = 'C:\test\PlexAdvisor\data\tunnel-client\v0.0.16\tunnel-client.exe'
$launcher = 'C:\test\PlexAdvisor\scripts\start_tunnel.ps1'
$tunnelArguments = @{Port=8766;Executables=@($tunnel);LauncherPath=$launcher;DescendantExecutables=@($tunnel)}
$tunnelProcesses = @(
    (New-TestProcess 40 1 'C:\PowerShell\pwsh.exe' ('pwsh.exe -NoProfile -File "'+$launcher+'"') 'pwsh.exe'),
    (New-TestProcess 41 40 $tunnel ('"'+$tunnel+'" run --profile-file profile.yaml') 'tunnel-client.exe'),
    (New-TestProcess 50 1 'C:\other\tunnel-client.exe' 'tunnel-client.exe run' 'tunnel-client.exe')
)
Assert-Equal (Get-PaServiceState -Processes $tunnelProcesses -Listeners @((New-TestListener 8766 41)) @tunnelArguments).State 'listening' 'launcher plus tunnel is one instance; unrelated tunnel excluded'
Assert-Equal (Get-PaServiceState -Processes @($tunnelProcesses[0]) -Listeners @() @tunnelArguments).State 'starting' 'existing launcher prevents duplicate startup'
Assert-Refuses { Get-PaServiceState -Processes $tunnelProcesses -Listeners @((New-TestListener 8766 50)) @tunnelArguments } 'foreign tunnel owner'
Write-Output 'PASS: 11 startup identity/ownership cases; no services, sockets, credentials, or settings touched.'
