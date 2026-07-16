<#
.SYNOPSIS
    Start Memorist services (Docker Compose) and open the browser.
.PARAMETER Mode
    Optional explicit override. Otherwise reads MEMORIST_MODE from .env.
.PARAMETER NoBrowser
    Do not open the browser after services are healthy.
#>
[CmdletBinding()]
param(
    [ValidateSet('lite', 'full')][string]$Mode,
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root

if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-MemoristLog 'No .env found. Run Install-Memorist.ps1 first.' 'FAIL'
    exit 1
}
if ([string]::IsNullOrWhiteSpace($Mode)) { $Mode = Get-MemoristInstalledMode -Root $root }
Show-MemoristBanner -Subtitle ("Start  -  mode: {0}" -f $Mode)
$docker = Test-MemoristDocker
if (-not $docker.Ready) {
    Write-MemoristLog $docker.Message 'FAIL'
    exit 1
}
foreach ($dir in @('data', 'objects', 'imports', 'exports', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $dir) | Out-Null
}
$composeFile = Get-MemoristComposeFile -Root $root
Write-MemoristLog 'Starting services...' 'STEP'
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments @('up', '-d', '--build', '--remove-orphans')
if ($rc -ne 0) { Write-MemoristLog 'Failed to start. See Show-Memorist-Logs.ps1.' 'FAIL'; exit 1 }

$webPortValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'OPEN_WEBUI_PORT'
$corePortValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'MEMORIST_PORT'
$webPort = if ($webPortValue) { [int]$webPortValue } else { 3000 }
$corePort = if ($corePortValue) { [int]$corePortValue } else { 8777 }
$coreOk = Wait-MemoristService -Name 'Memorist Core' -Url ("http://localhost:{0}/memcore/health" -f $corePort) -TimeoutSec 180
$webOk = Wait-MemoristService -Name 'Open WebUI' -Url ("http://localhost:{0}/health" -f $webPort) -TimeoutSec 180
if ($Mode -eq 'full' -and $coreOk) {
    $coreOk = Test-MemoristFullReadiness -Url ("http://localhost:{0}/memcore/config/effective" -f $corePort)
}
if (-not ($coreOk -and $webOk)) { Write-MemoristLog 'Runtime readiness verification failed.' 'FAIL'; exit 1 }
if ($webOk -and -not $NoBrowser) { Open-MemoristBrowser -Url "http://localhost:$webPort" }
Write-MemoristLog ("Open WebUI: http://localhost:{0}" -f $webPort) 'OK'
