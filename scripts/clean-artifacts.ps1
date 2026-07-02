param(
    [switch]$Apply,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ArgsList = @()
if ($Apply) { $ArgsList += "--apply" }
if ($Json) { $ArgsList += "--json" }
python (Join-Path $Root "scripts\clean_artifacts.py") @ArgsList
