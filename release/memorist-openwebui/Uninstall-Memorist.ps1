<#
.SYNOPSIS
    Remove Memorist containers and images. Optionally remove data volumes.
.DESCRIPTION
    Stops and removes containers created by this package. By default, memory
    DATA VOLUMES ARE PRESERVED so you can reinstall without losing memories.
    Pass -PurgeData to also delete volumes and local data folders.
.PARAMETER PurgeData
    Also delete all memory data (volumes + local folders). DESTRUCTIVE.
.PARAMETER RemoveImages
    Also remove the pulled/built Docker images.
.PARAMETER Force
    Skip the interactive confirmation.
#>
[CmdletBinding()]
param(
    [switch]$PurgeData,
    [switch]$RemoveImages,
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root
$Mode = Get-MemoristInstalledMode -Root $root

Show-MemoristBanner -Subtitle 'Uninstall'
if ($PurgeData) {
    Write-Host '  -PurgeData will DELETE all captured memories and volumes. This cannot be undone.' -ForegroundColor Red
} else {
    Write-Host '  Containers will be removed. Your memory data volumes will be PRESERVED.' -ForegroundColor Yellow
}
Write-Host ''
if (-not $Force) {
    $expected = if ($PurgeData) { 'DELETE' } else { 'yes' }
    $confirm = Read-Host ("Type {0} to continue" -f $expected)
    if ($confirm -ne $expected) {
        Write-MemoristLog 'Cancelled.' 'OK'
        exit 0
    }
}

$docker = Test-MemoristDocker
if (-not $docker.Ready) { Write-MemoristLog $docker.Message 'FAIL'; exit 1 }
$composeFile = Get-MemoristComposeFile -Root $root

$downArgs = @('down')
if ($PurgeData) { $downArgs += '-v' }
if ($RemoveImages) { $downArgs += @('--rmi', 'local') }
Write-MemoristLog 'Removing containers...' 'STEP'
$downArgs += '--remove-orphans'
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments $downArgs
if ($rc -ne 0) {
    Write-MemoristLog 'Docker removal failed; data folders were not modified.' 'FAIL'
    exit 1
}

if ($PurgeData) {
    foreach ($dir in @('data', 'objects', 'imports', 'exports', 'logs')) {
        $target = Join-Path $root $dir
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-MemoristLog 'Removed local data folders.' 'OK'
}
Write-MemoristLog 'Uninstall complete. The package folder and .env remain; delete them manually if desired.' 'OK'
