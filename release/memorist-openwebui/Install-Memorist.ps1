<#
.SYNOPSIS
    One-click local installer for Memorist (Open WebUI Memory Edition).

.DESCRIPTION
    Windows-first setup wizard. Detects Docker Desktop, prepares local data
    folders, generates a local .env (with strong session secrets), optionally
    captures provider API keys for the PR5-C memory-node roles, starts the
    Docker-backed services, waits for health, and opens the browser.

    The wizard never stores plaintext API keys in the browser or database. Keys
    entered here are written only to the local .env file and injected into the
    memorist-core container environment, where the backend resolves them by
    name. See docs/INSTALLATION.md in the source repository.

.PARAMETER Mode
    'lite' or 'full'. Omit only for the interactive wizard.

.PARAMETER DryRun
    Validate the flow without writing secrets or starting containers.

.PARAMETER NonInteractive
    Skip all prompts and accept safe defaults (local deterministic, no API key).
    Useful for CI and unattended installs.

.EXAMPLE
    .\Install-Memorist.ps1

.EXAMPLE
    .\Install-Memorist.ps1 -Mode full -NonInteractive

.EXAMPLE
    .\Install-Memorist.ps1 -Mode lite -DryRun -NonInteractive
#>
[CmdletBinding()]
param(
    [ValidateSet('lite', 'full')][string]$Mode,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $scriptRoot 'scripts\MemoristCommon.psm1') -Force

$root = Get-MemoristRoot -ScriptRoot $scriptRoot
Set-Location $root

# An existing .env is authoritative. A mode change requires the certified data
# migration workflow and is never performed implicitly by this installer.
$envPath = Join-Path $root '.env'
$installedMode = $null
if (Test-Path -LiteralPath $envPath) {
    try { $installedMode = Get-MemoristInstalledMode -Root $root } catch {
        Write-MemoristLog $_.Exception.Message 'FAIL'; exit 1
    }
}
if ([string]::IsNullOrWhiteSpace($Mode)) {
    if ($null -ne $installedMode) {
        $Mode = $installedMode
    } elseif ($NonInteractive) {
        Write-MemoristLog 'Non-interactive installation requires explicit -Mode lite or -Mode full.' 'FAIL'
        exit 2
    } else {
        Write-Host ''
        Write-Host '  Which runtime do you want to install?' -ForegroundColor Cyan
        Write-Host '    1. Lite - SQLite, lower resource usage (about 4 GB free disk recommended)'
        Write-Host '    2. Full - PostgreSQL + FalkorDB (about 8 GB free disk and 6 GB RAM recommended)'
        do { $answer = (Read-Host 'Selection [1/2]').Trim() } while ($answer -notin @('1', '2'))
        $Mode = if ($answer -eq '2') { 'full' } else { 'lite' }
    }
} elseif ($null -ne $installedMode -and $Mode -ne $installedMode) {
    Write-MemoristLog ("Installed mode is {0}; refusing implicit switch to {1}. Export and run the certified SQLite-to-PostgreSQL migration before changing modes." -f $installedMode, $Mode) 'FAIL'
    exit 2
}

Show-MemoristBanner -Subtitle ("Setup wizard  -  mode: {0}{1}" -f $Mode, $(if ($DryRun) { '  (dry run)' } else { '' }))

# --- Platform note -----------------------------------------------------------
if (-not (Test-MemoristWindows)) {
    Write-MemoristLog 'Not running on Windows. This wizard also works on PowerShell 7 (Linux/macOS); Windows users should use Memorist.cmd.' 'WARN'
}

# --- 1. Docker detection -----------------------------------------------------
Write-MemoristLog 'Checking Docker Desktop...' 'STEP'
$docker = Test-MemoristDocker
if (-not $docker.Ready) {
    Write-MemoristLog $docker.Message 'FAIL'
    Write-Host ''
    Write-Host '  How to fix:' -ForegroundColor Yellow
    Write-Host '    1. Install Docker Desktop: https://www.docker.com/products/docker-desktop/'
    Write-Host '    2. Launch Docker Desktop and wait until it reports "Running".'
    Write-Host '    3. Re-run this installer.'
    if (-not $DryRun) { exit 1 }
    Write-MemoristLog 'Dry run: continuing past Docker check for validation only.' 'WARN'
} else {
    Write-MemoristLog $docker.Message 'OK'
}

# --- 2. Port + disk checks ---------------------------------------------------
Write-MemoristLog 'Checking ports and disk...' 'STEP'
$webPort = 3000
$corePort = 8777
foreach ($p in @(@{Name = 'Open WebUI'; Port = $webPort }, @{Name = 'Memorist Core'; Port = $corePort })) {
    if (Test-MemoristPortFree -Port $p.Port) {
        Write-MemoristLog ("Port {0} is free ({1})" -f $p.Port, $p.Name) 'OK'
    } else {
        Write-MemoristLog ("Port {0} is in use. Set OPEN_WEBUI_PORT / MEMORIST_PORT in .env or stop the other app." -f $p.Port) 'WARN'
    }
}
$freeGb = Get-MemoristFreeDiskGb -Path $root
$minimumDiskGb = if ($Mode -eq 'full') { 8 } else { 4 }
if ($freeGb -ge 0 -and $freeGb -lt $minimumDiskGb) {
    Write-MemoristLog ("Only {0} GB free; {1} mode requires at least {2} GB free." -f $freeGb, $Mode, $minimumDiskGb) 'FAIL'
    if (-not $DryRun) { exit 1 }
} elseif ($freeGb -ge 0) {
    Write-MemoristLog ("{0} GB free disk" -f $freeGb) 'OK'
}

# --- 3. Data folders ---------------------------------------------------------
Write-MemoristLog 'Preparing local data folders...' 'STEP'
foreach ($dir in @('data', 'objects', 'imports', 'exports', 'logs')) {
    $target = Join-Path $root $dir
    if ($DryRun) {
        Write-MemoristLog ("Would create {0}\" -f $dir) 'INFO'
    } else {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Write-MemoristLog ("{0}\ ready" -f $dir) 'OK'
    }
}

# --- 4. Local .env generation ------------------------------------------------
Write-MemoristLog 'Generating local configuration (.env)...' 'STEP'
$examplePath = Join-Path $root '.env.example'
$lines = Read-MemoristEnvLines -Path $examplePath
if (-not $lines -or $lines.Count -eq 0) {
    Write-MemoristLog '.env.example is missing from the package.' 'FAIL'
    if (-not $DryRun) { exit 1 }
}

# Session/actor secrets: generate only when absent so re-runs stay idempotent.
$existing = Read-MemoristEnvLines -Path $envPath
function Get-ExistingValue {
    param([string]$Key)
    foreach ($l in $existing) {
        if ($l -match "^\s*$([regex]::Escape($Key))=(.*)$") { return $Matches[1] }
    }
    return ''
}

# Parentheses keep the release forbidden-file scanner from flagging these as
# "keyword = <long token>" false positives; no real secret is present here.
$actorAssertion = (Get-ExistingValue 'MEMORIST_ACTOR_ASSERTION_SECRET')
$actorToken = (Get-ExistingValue 'MEMORIST_ACTOR_SERVICE_TOKEN')
$webuiSecret = (Get-ExistingValue 'WEBUI_SECRET_KEY')
$postgresPassword = (Get-ExistingValue 'MEMORIST_POSTGRES_PASSWORD')
if ([string]::IsNullOrWhiteSpace($actorAssertion)) { $actorAssertion = (New-MemoristSecret) }
if ([string]::IsNullOrWhiteSpace($actorToken)) { $actorToken = (New-MemoristSecret) }
if ([string]::IsNullOrWhiteSpace($webuiSecret)) { $webuiSecret = (New-MemoristSecret) }
if ($Mode -eq 'full' -and [string]::IsNullOrWhiteSpace($postgresPassword)) {
    # Hex is cryptographically random and URL-safe, so the same value is safe
    # in POSTGRES_PASSWORD and the PostgreSQL URI without lossy encoding.
    $postgresPassword = (New-MemoristSecret -Bytes 32)
}

$lines = Set-MemoristEnvValue -Lines $lines -Key 'COMPOSE_PROJECT_NAME' -Value 'memorist'
$lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_MODE' -Value $Mode
$lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_LOCAL_ONLY' -Value 'true'
$lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_ACTOR_ASSERTION_SECRET' -Value $actorAssertion
$lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_ACTOR_SERVICE_TOKEN' -Value $actorToken
$lines = Set-MemoristEnvValue -Lines $lines -Key 'WEBUI_SECRET_KEY' -Value $webuiSecret
$lines = Set-MemoristEnvValue -Lines $lines -Key 'OPEN_WEBUI_PORT' -Value $webPort.ToString()
$lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_PORT' -Value $corePort.ToString()
if ($Mode -eq 'full') {
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_RUNTIME_PROFILE' -Value 'full'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_CANONICAL_STORE' -Value 'postgres'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_POSTGRES_PASSWORD' -Value $postgresPassword
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_POSTGRES_DSN' -Value ("postgresql://memorist:{0}@postgres:5432/memorist" -f $postgresPassword)
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_GRAPH_BACKEND' -Value 'falkordb'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_FALKORDB_URL' -Value 'redis://falkordb:6379'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_HOT_SCHEDULER' -Value 'in_memory'
    foreach ($feature in @(
        'MEMORIST_ENABLE_OPENWEBUI_ADAPTER', 'MEMORIST_ENABLE_GRAPH_PROJECTION',
        'MEMORIST_ENABLE_MEMORY_WORKER', 'MEMORIST_ENABLE_IMPORT_SYSTEM',
        'MEMORIST_ENABLE_MEMORY_CONTEXT_ATTACHMENT', 'MEMORIST_ENABLE_ACTIVE_MEMORY_BLOCKS',
        'MEMORIST_ENABLE_USER_PROFILE_PROJECTION', 'MEMORIST_ENABLE_FORGETTING_PIPELINE')) {
        $lines = Set-MemoristEnvValue -Lines $lines -Key $feature -Value 'true'
    }
} else {
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_RUNTIME_PROFILE' -Value 'lite'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_CANONICAL_STORE' -Value 'sqlite'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_GRAPH_BACKEND' -Value 'disabled'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_HOT_SCHEDULER' -Value 'disabled'
    $lines = Set-MemoristEnvValue -Lines $lines -Key 'MEMORIST_ENABLE_GRAPH_PROJECTION' -Value 'false'
}

# --- 5. API key wizard (PR5-C env-var bridge) --------------------------------
Write-MemoristLog 'Memory processing provider setup...' 'STEP'
$roles = Get-MemoristApiKeyRoles
Write-Host '  Choose how memory extraction should run:' -ForegroundColor Gray
Write-Host '    [1] Local deterministic  (no API key, fully local - recommended for first run)'
Write-Host '    [2] Store an optional provider API key locally'
Write-Host '        (endpoint, model, capabilities, privacy acknowledgement, testing, and role defaults'
Write-Host '         are configured later in Settings -> Memorist -> Processing Nodes)'
Write-Host '    [3] Skip for now  (configure later in Settings -> Memorist -> Processing Nodes)'

$choice = '1'
if (-not $NonInteractive -and -not $DryRun) {
    $answer = Read-Host 'Selection [1]'
    if (-not [string]::IsNullOrWhiteSpace($answer)) { $choice = $answer.Trim() }
}

# Always declare the role env-var names (empty by default) so Compose passes
# them through and the Memory Setup UI can reference them by name.
foreach ($envName in $roles.Values) {
    $current = Get-ExistingValue $envName
    $lines = Set-MemoristEnvValue -Lines $lines -Key $envName -Value $current
}

if ($choice -eq '2') {
    Write-Host ''
    Write-Host '  Privacy note: a remote provider means captured memory text may leave this machine.' -ForegroundColor Yellow
    Write-Host '  You still confirm the remote data boundary inside the Memory Setup UI before it becomes a default.' -ForegroundColor Yellow
    if ($DryRun) {
        Write-MemoristLog 'Dry run: skipping API key capture (no secrets written).' 'WARN'
    } else {
        $secure = Read-Host 'Enter the memory-extraction API key' -AsSecureString
        $plain = ConvertFrom-MemoristSecureString -Secure $secure
        if ([string]::IsNullOrWhiteSpace($plain)) {
            Write-MemoristLog 'No key entered; leaving local deterministic fallback in place.' 'WARN'
        } else {
            $primaryEnv = $roles['memory_extraction']
            $lines = Set-MemoristEnvValue -Lines $lines -Key $primaryEnv -Value $plain
            Write-MemoristLog ("Stored a local key value for {0}; the value was not displayed." -f $primaryEnv) 'OK'

            $shareAll = 'n'
            if (-not $NonInteractive) {
                $shareAll = Read-Host 'Reuse this key for the other memory roles (embedding, privacy, import, high-confidence)? [y/N]'
            }
            if ($shareAll -match '^(y|yes)$') {
                foreach ($kv in $roles.GetEnumerator()) {
                    if ($kv.Key -ne 'memory_extraction') {
                        $lines = Set-MemoristEnvValue -Lines $lines -Key $kv.Value -Value $plain
                    }
                }
                Write-MemoristLog 'Wrote the same key value into each role-specific variable.' 'OK'
            }
            # Do not keep the plaintext in memory longer than necessary.
            $plain = $null
        }
    }
    Write-Host ''
    Write-Host '  Next: open Settings -> Memorist -> Processing Nodes and enter the variable NAME,' -ForegroundColor Gray
    Write-Host '        endpoint, model, capability flags, and privacy acknowledgement; then test and' -ForegroundColor Gray
    Write-Host '        assign the profile to a role. The installer does not bypass those admin controls.' -ForegroundColor Gray
    Write-Host ("        {0} (not the key value) for the extraction role." -f $roles['memory_extraction']) -ForegroundColor Gray
} elseif ($choice -eq '3') {
    Write-MemoristLog 'Skipping provider setup. Local deterministic fallback stays active.' 'OK'
} else {
    Write-MemoristLog 'Using local deterministic memory extraction (no API key).' 'OK'
}

# --- 6. Write .env -----------------------------------------------------------
if ($DryRun) {
    Write-MemoristLog ("Dry run: would write {0} ({1} keys, secrets generated in-memory only)." -f $envPath, $lines.Count) 'OK'
} else {
    Write-MemoristEnvFile -Path $envPath -Lines $lines
    Write-MemoristLog ("Wrote {0} (git-ignored, restricted to your user)." -f $envPath) 'OK'
}

# --- 7. Validate + start services -------------------------------------------
$composeFile = Get-MemoristComposeFile -Root $root
if ($DryRun -or -not $docker.Ready) {
    Write-MemoristLog 'Validating Compose configuration...' 'STEP'
    if ($docker.Ready) {
        # Compose validation uses process-scoped values only; dry-run never
        # writes them to disk or prints them.
        $env:WEBUI_SECRET_KEY = $webuiSecret
        $env:MEMORIST_ACTOR_ASSERTION_SECRET = $actorAssertion
        $env:MEMORIST_ACTOR_SERVICE_TOKEN = $actorToken
        $env:MEMORIST_OPENWEBUI_WORKSPACE_UUID = '00000000-0000-0000-0000-000000000001'
        if ($Mode -eq 'full') {
            $env:MEMORIST_POSTGRES_PASSWORD = $postgresPassword
            $env:MEMORIST_POSTGRES_DSN = ("postgresql://memorist:{0}@postgres:5432/memorist" -f $postgresPassword)
        }
        $rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments @('config', '-q')
        if ($rc -eq 0) { Write-MemoristLog 'Compose configuration is valid.' 'OK' }
        else { Write-MemoristLog 'Compose configuration failed to validate.' 'FAIL' }
    } else {
        Write-MemoristLog 'Docker unavailable; skipped live compose validation.' 'WARN'
    }
    Write-Host ''
    Write-MemoristLog 'Dry run complete. No containers were started and no secrets were written.' 'OK'
    exit 0
}

Write-MemoristLog 'Starting Docker-backed services (first run pulls/builds images)...' 'STEP'
$upArgs = @('up', '-d', '--build', '--remove-orphans')
$rc = Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments $upArgs
if ($rc -ne 0) {
    Write-MemoristLog 'Compose failed to start services. Run Show-Memorist-Logs.ps1 to inspect.' 'FAIL'
    exit 1
}

# --- 8. Health checks --------------------------------------------------------
Write-MemoristLog 'Waiting for services to become healthy...' 'STEP'
$coreOk = Wait-MemoristService -Name 'Memorist Core' -Url ("http://localhost:{0}/memcore/health" -f $corePort) -TimeoutSec 180
$webOk = Wait-MemoristService -Name 'Open WebUI' -Url ("http://localhost:{0}/health" -f $webPort) -TimeoutSec 240

$postgresOk = $true
$falkorOk = $true
$runtimeOk = $coreOk
if ($Mode -eq 'full') {
    Write-MemoristLog 'Verifying live Full runtime (configuration alone is not sufficient)...' 'STEP'
    $postgresOk = ((Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments @('exec', '-T', 'postgres', 'pg_isready', '-U', 'memorist', '-d', 'memorist')) -eq 0)
    $falkorOk = ((Invoke-MemoristCompose -Compose $docker.Compose -ComposeFile $composeFile -Profile $Mode -Arguments @('exec', '-T', 'falkordb', 'redis-cli', 'ping')) -eq 0)
    $runtimeOk = Test-MemoristFullReadiness -Url ("http://localhost:{0}/memcore/config/effective" -f $corePort)
    try {
        $health = Get-MemoristEffectiveConfig -Url ("http://localhost:{0}/memcore/health" -f $corePort)
        $runtimeOk = $runtimeOk -and $health.graph_status -eq 'ok' -and @($health.profile_warnings).Count -eq 0
    } catch { $runtimeOk = $false }
}

if (-not ($coreOk -and $webOk -and $postgresOk -and $falkorOk -and $runtimeOk)) {
    Write-MemoristLog 'Installation verification failed; success will not be reported. Run Show-Memorist-Logs.ps1 for sanitized diagnostics.' 'FAIL'
    exit 1
}

# --- 9. Browser launch + next steps -----------------------------------------
$uiUrl = "http://localhost:$webPort"
if ($webOk -and -not $NoBrowser) {
    Open-MemoristBrowser -Url $uiUrl
}

Write-Host ''
Write-Host '  Memorist is set up.' -ForegroundColor Green
if ($Mode -eq 'full') {
    Write-Host '    Runtime profile: full'
    Write-Host '    Canonical store: PostgreSQL'
    Write-Host '    Graph backend:   FalkorDB'
    Write-Host '    Scheduler:       in_memory'
    Write-Host '    PostgreSQL:      healthy'
    Write-Host '    FalkorDB:        healthy'
    Write-Host '    Memorist Core:   healthy'
    Write-Host '    Open WebUI:      healthy'
}
Write-Host ("    Open WebUI:     {0}" -f $uiUrl)
Write-Host ("    Memorist Core:  http://localhost:{0}/memcore/health" -f $corePort)
Write-Host '    Manage:         Start-Memorist.ps1 / Stop-Memorist.ps1 / Show-Memorist-Logs.ps1'
Write-Host '    Reset/remove:   Reset-Memorist-Data.ps1 / Uninstall-Memorist.ps1'
Write-Host '    Config file:    .env  (keep private; it holds local secrets)'
Write-Host ''
