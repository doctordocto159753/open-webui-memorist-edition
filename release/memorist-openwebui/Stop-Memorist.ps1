<#
.SYNOPSIS
    Stop Memorist services. Volumes and data are preserved.
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root

Show-MemoristBanner -Subtitle 'Stop'
$docker = Test-MemoristDocker
if (-not $docker.Ready) { Write-MemoristLog $docker.Message 'FAIL'; exit 1 }
$composeFile = Get-MemoristComposeFile -Root $root
Write-MemoristLog 'Stopping services (data volumes are kept)...' 'STEP'
# 'down' without -v preserves named volumes and local data.
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile 'lite' -Arguments @('down')
if ($rc -eq 0) { Write-MemoristLog 'Stopped. Your memory data is preserved.' 'OK' }
else { Write-MemoristLog 'Stop returned a non-zero exit code.' 'WARN' }
