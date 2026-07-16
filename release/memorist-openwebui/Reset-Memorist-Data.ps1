<#
.SYNOPSIS
    Delete all Memorist memory data (containers, named volumes, local folders).
.DESCRIPTION
    DESTRUCTIVE. Removes captured memories, imports, exports, and objects.
    Requires an explicit confirmation unless -Force is supplied. The .env file
    (and its secrets) is left in place unless -IncludeEnv is passed.
.PARAMETER Force
    Skip the interactive confirmation (for scripted use).
.PARAMETER IncludeEnv
    Also delete the local .env configuration file.
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$IncludeEnv
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force
$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root
$Mode = Get-MemoristInstalledMode -Root $root

Show-MemoristBanner -Subtitle 'Reset data (DESTRUCTIVE)'
Write-Host '  This permanently deletes all captured memories, imports, exports,' -ForegroundColor Red
Write-Host '  object storage, and database volumes. This cannot be undone.' -ForegroundColor Red
Write-Host ''

if (-not $Force) {
    $confirm = Read-Host 'Type DELETE to confirm data reset'
    if ($confirm -ne 'DELETE') {
        Write-MemoristLog 'Cancelled. No data was deleted.' 'OK'
        exit 0
    }
}

$docker = Test-MemoristDocker
if (-not $docker.Ready) {
    Write-MemoristLog $docker.Message 'FAIL'
    Write-MemoristLog 'Reset aborted before deleting local folders because Docker volumes could not be removed.' 'FAIL'
    exit 1
}
$composeFile = Get-MemoristComposeFile -Root $root
Write-MemoristLog 'Stopping services and removing named volumes...' 'STEP'
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments @('down', '-v', '--remove-orphans')
if ($rc -ne 0) {
    Write-MemoristLog 'Docker volume removal failed; reset aborted before deleting local folders.' 'FAIL'
    exit 1
}

foreach ($dir in @('data', 'objects', 'imports', 'exports', 'logs')) {
    $target = Join-Path $root $dir
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-MemoristLog ("Removed {0}\" -f $dir) 'OK'
    }
}
if ($IncludeEnv) {
    $envPath = Join-Path $root '.env'
    if (Test-Path -LiteralPath $envPath) {
        Remove-Item -LiteralPath $envPath -Force
        Write-MemoristLog 'Removed .env (secrets).' 'OK'
    }
}
Write-MemoristLog 'Reset complete.' 'OK'
