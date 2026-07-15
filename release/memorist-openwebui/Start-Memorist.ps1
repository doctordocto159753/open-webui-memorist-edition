<#
.SYNOPSIS
    Start Memorist services (Docker Compose) and open the browser.
.PARAMETER Mode
    'lite' (default) or 'full'.
.PARAMETER NoBrowser
    Do not open the browser after services are healthy.
#>
[CmdletBinding()]
param(
    [ValidateSet('lite', 'full')][string]$Mode = 'lite',
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root

Show-MemoristBanner -Subtitle ("Start  -  mode: {0}" -f $Mode)

if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-MemoristLog 'No .env found. Run Install-Memorist.ps1 first.' 'FAIL'
    exit 1
}
$docker = Test-MemoristDocker
if (-not $docker.Ready) {
    Write-MemoristLog $docker.Message 'FAIL'
    exit 1
}
foreach ($dir in @('data', 'objects', 'imports', 'exports', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $dir) | Out-Null
}
$composeFile = Get-MemoristComposeFile -Root $root
$services = if ($Mode -eq 'full') { @() } else { @('memorist-core', 'open-webui') }
Write-MemoristLog 'Starting services...' 'STEP'
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments (@('up', '-d', '--build') + $services)
if ($rc -ne 0) { Write-MemoristLog 'Failed to start. See Show-Memorist-Logs.ps1.' 'FAIL'; exit 1 }

$webPort = 3000
$corePort = 8777
Wait-MemoristService -Name 'Memorist Core' -Url ("http://localhost:{0}/memcore/health" -f $corePort) -TimeoutSec 120 | Out-Null
$webOk = Wait-MemoristService -Name 'Open WebUI' -Url ("http://localhost:{0}/health" -f $webPort) -TimeoutSec 180
if ($webOk -and -not $NoBrowser) { Open-MemoristBrowser -Url "http://localhost:$webPort" }
Write-MemoristLog ("Open WebUI: http://localhost:{0}" -f $webPort) 'OK'
