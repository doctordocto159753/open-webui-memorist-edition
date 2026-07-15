<#
.SYNOPSIS
    Restart Memorist services (stop, then start). Data is preserved.
.PARAMETER Mode
    'lite' (default) or 'full'.
#>
[CmdletBinding()]
param([ValidateSet('lite', 'full')][string]$Mode = 'lite')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptRoot 'Stop-Memorist.ps1')
& (Join-Path $scriptRoot 'Start-Memorist.ps1') -Mode $Mode
