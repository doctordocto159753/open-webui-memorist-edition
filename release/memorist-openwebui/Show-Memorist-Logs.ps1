<#
.SYNOPSIS
    Follow Memorist service logs.
.PARAMETER Service
    Optional single service name (memorist-core, open-webui, falkordb).
.PARAMETER Tail
    Number of trailing lines to show (default 200).
.PARAMETER NoFollow
    Print the requested tail and exit (useful for diagnostics and automation).
#>
[CmdletBinding()]
param(
    [string]$Service = '',
    [int]$Tail = 200,
    [switch]$NoFollow
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root
$Mode = Get-MemoristInstalledMode -Root $root

$docker = Test-MemoristDocker
if (-not $docker.Ready) { Write-MemoristLog $docker.Message 'FAIL'; exit 1 }
$composeFile = Get-MemoristComposeFile -Root $root
$logArgs = @('logs', '--tail', $Tail.ToString())
if (-not $NoFollow) { $logArgs += '-f' }
if (-not [string]::IsNullOrWhiteSpace($Service)) { $logArgs += $Service }
Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments $logArgs | Out-Null
