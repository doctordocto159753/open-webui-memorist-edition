<#
.SYNOPSIS
    Restart Memorist services (stop, then start). Data is preserved.
.PARAMETER Mode
    Optional explicit override. Otherwise reads MEMORIST_MODE from .env.
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

# The installed mode in .env is authoritative. Refuse a conflicting -Mode
# before stopping anything, so a typo cannot take the product down.
$installedMode = Get-MemoristInstalledMode -Root $root
if (-not [string]::IsNullOrWhiteSpace($Mode) -and $Mode -ne $installedMode) {
    Write-Error ("This installation is configured for '{0}' mode; refusing to restart in '{1}' mode. Switching modes is a data migration: run Install-Memorist.ps1 -Mode {1} to migrate explicitly." -f $installedMode, $Mode)
    exit 1
}

& (Join-Path $scriptRoot 'Stop-Memorist.ps1')
if ($LASTEXITCODE -ne 0) {
    Write-Error "Restart aborted because Stop-Memorist.ps1 failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$startArgs = @{}
if (-not [string]::IsNullOrWhiteSpace($Mode)) { $startArgs.Mode = $Mode }
if ($NoBrowser) { $startArgs.NoBrowser = $true }
& (Join-Path $scriptRoot 'Start-Memorist.ps1') @startArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Restart failed because Start-Memorist.ps1 exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}
