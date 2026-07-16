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
