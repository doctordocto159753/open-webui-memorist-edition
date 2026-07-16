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

& (Join-Path $root 'Install-Memorist.ps1') -Mode full -NonInteractive -NoBrowser
if ($LASTEXITCODE) { exit $LASTEXITCODE }
if ((Get-MemoristInstalledMode -Root $root) -ne 'full') { throw 'Installed mode did not persist as Full.' }
& (Join-Path $root 'Stop-Memorist.ps1')
& (Join-Path $root 'Start-Memorist.ps1') -NoBrowser
& (Join-Path $root 'Restart-Memorist.ps1') -NoBrowser

$portValue = Get-MemoristEnvValue -Path (Join-Path $root '.env') -Key 'MEMORIST_PORT'
$port = if ($portValue) { [int]$portValue } else { 8777 }
if (-not (Test-MemoristFullReadiness -Url "http://localhost:$port/memcore/config/effective")) {
    throw 'Strict Full readiness check failed after lifecycle test.'
}
Write-MemoristLog 'Windows Full install and lifecycle smoke passed.' 'OK'
