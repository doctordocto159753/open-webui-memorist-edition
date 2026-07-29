<#
.SYNOPSIS
    Start Memorist services (Docker Compose) and open the browser.
.PARAMETER Mode
    Optional explicit override. Otherwise reads MEMORIST_MODE from .env.
.PARAMETER NoBrowser
    Do not open the browser after services are healthy.
.PARAMETER NoBuild
    Start only from existing images. Intended for controlled CI after one
    explicit package image build; local starts continue to build by default.
#>
[CmdletBinding()]
param(
    [ValidateSet('lite', 'full')][string]$Mode,
    [switch]$NoBrowser,
    [switch]$NoBuild
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
$installedMode = Get-MemoristInstalledMode -Root $root
if (-not [string]::IsNullOrWhiteSpace($Mode) -and $Mode -ne $installedMode) {
    Write-MemoristLog ("This installation is configured for '{0}' mode; refusing to start in '{1}' mode." -f $installedMode, $Mode) 'FAIL'
    Write-MemoristLog ("Switching modes is a data migration, not a start-time flag. Run Install-Memorist.ps1 -Mode {0} to migrate explicitly; your existing data volumes are preserved until you do." -f $Mode) 'INFO'
    exit 1
}
$Mode = $installedMode
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
$startArguments = @('up', '-d')
if (-not $NoBuild) { $startArguments += '--build' }
$startArguments += '--remove-orphans'
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments $startArguments
if ($rc -ne 0) { Write-MemoristLog 'Failed to start. See Show-Memorist-Logs.ps1.' 'FAIL'; exit 1 }

$webPortValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'OPEN_WEBUI_PORT'
$corePortValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'MEMORIST_PORT'
$webPort = if ($webPortValue) { [int]$webPortValue } else { 3000 }
$corePort = if ($corePortValue) { [int]$corePortValue } else { 8777 }
$coreOk = Wait-MemoristService -Name 'Memorist Core' -Url ("http://localhost:{0}/memcore/health" -f $corePort) -TimeoutSec 180
$webOk = Wait-MemoristService -Name 'Open WebUI' -Url ("http://localhost:{0}/health" -f $webPort) -TimeoutSec 180

if ($webOk) {
    # A healthy Open WebUI without reachable Memorist proxy routes is a broken
    # product pretending to be fine. Fail Start loudly in that case.
    if (-not (Test-MemoristProxyRoutes -WebPort $webPort)) {
        Write-MemoristLog 'Open WebUI is healthy but the Memorist proxy routes (/api/v1/memorist) are not reachable.' 'FAIL'
        Write-MemoristLog 'The Memorist integration failed to mount. Run Show-Memorist-Logs.ps1 and inspect the open-webui service log.' 'INFO'
        exit 1
    }
    Write-MemoristLog 'Memorist proxy routes verified (/api/v1/memorist responds with authenticated-API status).' 'OK'
}

if ($Mode -eq 'full' -and $coreOk) {
    $coreOk = Test-MemoristFullReadiness -Url ("http://localhost:{0}/memcore/config/effective" -f $corePort)
    if ($coreOk) {
        try {
            $health = Get-MemoristEffectiveConfig -Url ("http://localhost:{0}/memcore/health" -f $corePort)
            $coreOk = $health.graph_status -eq 'ok' -and @($health.profile_warnings).Count -eq 0
        } catch {
            $coreOk = $false
        }
    }
    if ($coreOk) {
        $postgresOk = Test-MemoristComposeService -Compose $docker.Compose -ComposeFile $composeFile -Mode $Mode -Arguments @('exec', '-T', 'postgres', 'pg_isready', '-U', 'memorist', '-d', 'memorist')
        $falkorOk = Test-MemoristComposeService -Compose $docker.Compose -ComposeFile $composeFile -Mode $Mode -Arguments @('exec', '-T', 'falkordb', 'redis-cli', 'ping')
        $coreOk = $postgresOk -and $falkorOk
    }
}

if (-not ($coreOk -and $webOk)) { Write-MemoristLog 'Runtime readiness verification failed.' 'FAIL'; exit 1 }
if ($webOk -and -not $NoBrowser) { Open-MemoristBrowser -Url "http://localhost:$webPort" }
Write-MemoristLog ("Open WebUI: http://localhost:{0}" -f $webPort) 'OK'
