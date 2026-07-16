<#
.SYNOPSIS
    Operator-run Windows Docker Desktop certification for an extracted package.
.DESCRIPTION
    Run only in a disposable test installation. It installs Full without a
    browser, verifies live effective configuration, exercises stop/start/restart,
    and leaves the data-preserving installation running for inspection.
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $root 'scripts\MemoristCommon.psm1') -Force

function Invoke-MemoristCheckedScript {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [hashtable]$Arguments = @{}
    )
    & $Path @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([IO.Path]::GetFileName($Path)) failed with exit code $LASTEXITCODE."
    }
}

Invoke-MemoristCheckedScript -Path (Join-Path $root 'Install-Memorist.ps1') -Arguments @{
    Mode = 'full'; NonInteractive = $true; NoBrowser = $true
}
if ((Get-MemoristInstalledMode -Root $root) -ne 'full') { throw 'Installed mode did not persist as Full.' }

Invoke-MemoristCheckedScript -Path (Join-Path $root 'Stop-Memorist.ps1')
Invoke-MemoristCheckedScript -Path (Join-Path $root 'Start-Memorist.ps1') -Arguments @{ NoBrowser = $true }
Invoke-MemoristCheckedScript -Path (Join-Path $root 'Restart-Memorist.ps1') -Arguments @{ NoBrowser = $true }

$portValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'MEMORIST_PORT'
$port = if ($portValue) { [int]$portValue } else { 8777 }
if (-not (Test-MemoristFullReadiness -Url "http://localhost:$port/memcore/config/effective")) {
    throw 'Strict Full readiness check failed after lifecycle test.'
}
$health = Get-MemoristEffectiveConfig -Url "http://localhost:$port/memcore/health"
if ($health.graph_status -ne 'ok' -or @($health.profile_warnings).Count -ne 0) {
    throw 'Full health is degraded after lifecycle test.'
}
Write-MemoristLog 'Windows Full install and lifecycle smoke passed.' 'OK'
