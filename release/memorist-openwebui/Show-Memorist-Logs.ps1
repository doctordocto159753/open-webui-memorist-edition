<#
.SYNOPSIS
    Follow Memorist service logs.
.PARAMETER Service
    Optional single service name (memorist-core, open-webui, falkordb).
.PARAMETER Tail
    Number of trailing lines to show (default 200).
#>
[CmdletBinding()]
param(
    [string]$Service = '',
    [int]$Tail = 200
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root

$docker = Test-MemoristDocker
if (-not $docker.Ready) { Write-MemoristLog $docker.Message 'FAIL'; exit 1 }
$composeFile = Get-MemoristComposeFile -Root $root
$logArgs = @('logs', '-f', '--tail', $Tail.ToString())
if (-not [string]::IsNullOrWhiteSpace($Service)) { $logArgs += $Service }
Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile 'lite' -Arguments $logArgs | Out-Null
