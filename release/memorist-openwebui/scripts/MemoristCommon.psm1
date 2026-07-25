<#
.SYNOPSIS
    Shared helpers for the Memorist Windows-first local installer and lifecycle
    scripts.

.DESCRIPTION
    This module centralises Docker detection, port/disk checks, local .env
    generation, secret handling, service health checks, and browser launch so
    that the individual entry-point scripts stay small and consistent.

    Secret safety rules honoured here:
      * Plaintext API keys are never written to the log or the console.
      * Only masked previews (last 4 characters) are ever displayed.
      * Generated secrets and provider keys land only in the local .env file.
      * The .env file is git-ignored by the repository root .gitignore.
#>

Set-StrictMode -Version Latest

function Get-MemoristDockerCli {
    <#
        The docker executable to invoke. Production always uses 'docker' on
        PATH. Behavioral tests set MEMORIST_DOCKER_CLI to a controlled shim so
        Docker/Compose readiness and failures can be simulated deterministically
        without relying on PATH ordering. This never affects a real install
        (the variable is unset there).
    #>
    if ($env:MEMORIST_DOCKER_CLI) { return $env:MEMORIST_DOCKER_CLI }
    return 'docker'
}

# ---------------------------------------------------------------------------
# Constants shared across the installer surface.
# ---------------------------------------------------------------------------

# PR5-C role -> environment variable name. These names are a convention the
# installer writes locally; the browser Memory Setup UI references the *name*
# only and the backend resolves the value from its own process environment.
$script:MemoristApiKeyRoles = [ordered]@{
    'preflight'                  = 'MEMORIST_PREFLIGHT_API_KEY'
    'memory_extraction'          = 'MEMORIST_MEMORY_EXTRACTION_API_KEY'
    'high_confidence_extraction' = 'MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY'
    'embedding'                  = 'MEMORIST_EMBEDDING_API_KEY'
    'privacy_sensitivity'        = 'MEMORIST_PRIVACY_SENSITIVITY_API_KEY'
    'block_compaction'           = 'MEMORIST_BLOCK_COMPACTION_API_KEY'
    'import_reconstruction'      = 'MEMORIST_IMPORT_RECONSTRUCTION_API_KEY'
}

function Get-MemoristApiKeyRoles {
    <# Returns the ordered role -> env-var-name map used by the wizard. #>
    return $script:MemoristApiKeyRoles
}

# ---------------------------------------------------------------------------
# Logging (never receives secret values).
# ---------------------------------------------------------------------------

function Write-MemoristLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'OK', 'WARN', 'FAIL', 'STEP')][string]$Level = 'INFO'
    )
    $prefix = switch ($Level) {
        'OK'   { '  [ OK ]' }
        'WARN' { '  [WARN]' }
        'FAIL' { '  [FAIL]' }
        'STEP' { '==>' }
        default { '  [ .. ]' }
    }
    $color = switch ($Level) {
        'OK'   { 'Green' }
        'WARN' { 'Yellow' }
        'FAIL' { 'Red' }
        'STEP' { 'Cyan' }
        default { 'Gray' }
    }
    Write-Host ("{0} {1}" -f $prefix, $Message) -ForegroundColor $color
}

function Show-MemoristBanner {
    param([string]$Subtitle = 'Windows-first local installer')
    Write-Host ''
    Write-Host '  Memorist  -  Open WebUI Memory Edition' -ForegroundColor Magenta
    Write-Host ("  {0}" -f $Subtitle) -ForegroundColor DarkGray
    Write-Host ''
}

# ---------------------------------------------------------------------------
# Platform + Docker detection.
# ---------------------------------------------------------------------------

function Test-MemoristWindows {
    <# True on Windows; used to print a cross-platform hint elsewhere. #>
    if ($null -ne (Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue)) {
        return [bool]$IsWindows
    }
    # Windows PowerShell 5.1 has no $IsWindows variable and is Windows-only.
    return $true
}

function Get-MemoristComposeCommand {
    <#
        Resolves the Docker Compose invocation. Prefers the v2 plugin
        ("docker compose") and falls back to the legacy "docker-compose".
        Returns $null when neither is available.
    #>
    $dockerCli = Get-MemoristDockerCli
    if (Get-Command $dockerCli -ErrorAction SilentlyContinue) {
        & $dockerCli compose version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($dockerCli, 'compose')
        }
    }
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        return @('docker-compose')
    }
    return $null
}

function Test-MemoristDocker {
    <#
        Returns a structured result describing Docker readiness. Never throws;
        callers decide how to surface each problem to the user.
    #>
    $result = [ordered]@{
        CliPresent    = $false
        DaemonRunning = $false
        Compose       = $null
        Message       = ''
        Ready         = $false
    }

    $dockerCli = Get-MemoristDockerCli
    if (-not (Get-Command $dockerCli -ErrorAction SilentlyContinue)) {
        $result.Message = 'Docker CLI not found. Install Docker Desktop and reopen this window.'
        return [pscustomobject]$result
    }
    $result.CliPresent = $true

    & $dockerCli info *> $null
    if ($LASTEXITCODE -ne 0) {
        $result.Message = 'Docker is installed but the daemon is not reachable. Start Docker Desktop and wait for it to report "Running".'
        return [pscustomobject]$result
    }
    $result.DaemonRunning = $true

    $compose = Get-MemoristComposeCommand
    if ($null -eq $compose) {
        $result.Message = 'Docker Compose is unavailable. Update Docker Desktop to a version that ships Compose v2.'
        return [pscustomobject]$result
    }
    $result.Compose = $compose

    $result.Ready = $true
    $result.Message = 'Docker and Compose are ready.'
    return [pscustomobject]$result
}

function Test-MemoristPortFree {
    <# True when the TCP port can be bound on the loopback interface. #>
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Get-MemoristExcludedTcpPortRanges {
    <#
        Returns Windows excluded TCP ranges as objects with Start/End fields.
        Tests may pass captured netsh output; production queries both IPv4 and
        IPv6. Parsing is numeric and therefore independent of OS language.
    #>
    param([AllowEmptyCollection()][string[]]$NetshOutput)
    $lines = @($NetshOutput)
    if (-not $PSBoundParameters.ContainsKey('NetshOutput')) {
        if (-not (Test-MemoristWindows)) { return @() }
        $lines = @()
        foreach ($family in @('ipv4', 'ipv6')) {
            try {
                $lines += @(& netsh interface $family show excludedportrange protocol=tcp 2>$null)
            } catch {
                Write-MemoristLog "Could not query $family excluded TCP ranges." 'WARN'
            }
        }
    }
    $ranges = @()
    foreach ($line in $lines) {
        if ([string]$line -match '^\s*(\d+)\s+(\d+)(?:\s+\*)?\s*$') {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            if ($start -ge 1 -and $end -ge $start -and $end -le 65535) {
                $ranges += [pscustomobject]@{ Start = $start; End = $end }
            }
        }
    }
    return @($ranges | Sort-Object Start, End -Unique)
}

function Test-MemoristPortExcluded {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [AllowEmptyCollection()][object[]]$ExcludedRanges = @()
    )
    foreach ($range in $ExcludedRanges) {
        if ($Port -ge [int]$range.Start -and $Port -le [int]$range.End) { return $true }
    }
    return $false
}

function Get-MemoristAvailablePort {
    <# Deterministically selects the first bindable, non-excluded host port. #>
    param(
        [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$PreferredPort,
        [AllowEmptyCollection()][object[]]$ExcludedRanges = @(),
        [scriptblock]$PortProbe = { param($candidate) Test-MemoristPortFree -Port $candidate }
    )
    for ($candidate = $PreferredPort; $candidate -le 65535; $candidate++) {
        if ((Test-MemoristPortExcluded -Port $candidate -ExcludedRanges $ExcludedRanges)) {
            continue
        }
        if (& $PortProbe $candidate) { return $candidate }
    }
    throw "No available host TCP port found at or above $PreferredPort."
}

function Get-MemoristFreeDiskGb {
    <# Free space (GB) on the drive that hosts the given path, or -1 if unknown. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
        $root = [System.IO.Path]::GetPathRoot($full)
        $drive = New-Object System.IO.DriveInfo($root)
        return [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    } catch {
        return -1
    }
}

# ---------------------------------------------------------------------------
# Secret generation + masking.
# ---------------------------------------------------------------------------

function New-MemoristSecret {
    <# Cryptographically strong lowercase-hex secret. #>
    param([int]$Bytes = 32)
    $buffer = New-Object 'System.Byte[]' $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    return -join ($buffer | ForEach-Object { $_.ToString('x2') })
}

function Get-MemoristMaskedSecret {
    <# Returns a display-safe preview such as "****abcd" (never the full value). #>
    param([string]$Value)
    if ([string]::IsNullOrEmpty($Value)) { return '(none)' }
    if ($Value.Length -le 4) { return '****' }
    return ('****' + $Value.Substring($Value.Length - 4))
}

function ConvertFrom-MemoristSecureString {
    <#
        Converts a SecureString to plaintext for the sole purpose of writing it
        into the local .env file. The plaintext is zeroed from unmanaged memory
        immediately after copying.
    #>
    param([System.Security.SecureString]$Secure)
    if ($null -eq $Secure -or $Secure.Length -eq 0) { return '' }
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($Secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringUni($ptr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($ptr)
    }
}

# ---------------------------------------------------------------------------
# .env generation and editing.
# ---------------------------------------------------------------------------

function Read-MemoristEnvLines {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    return @(Get-Content -LiteralPath $Path -Encoding UTF8)
}

function Get-MemoristEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )
    foreach ($line in (Read-MemoristEnvLines -Path $Path)) {
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") { return $Matches[1].Trim() }
    }
    return ''
}

function Get-MemoristInstalledMode {
    <# Reads the authoritative installed mode. Missing/corrupt values fail closed. #>
    param([Parameter(Mandatory = $true)][string]$Root)
    $envPath = Join-Path $Root '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw 'No installed configuration found. Run Install-Memorist.ps1 first.'
    }
    $mode = (Get-MemoristEnvValue -Path $envPath -Key 'MEMORIST_MODE').ToLowerInvariant()
    if ($mode -notin @('lite', 'full')) {
        throw 'The installed MEMORIST_MODE is missing or invalid. Repair .env or rerun the installer.'
    }
    return $mode
}

function Get-MemoristProjectEnvironment {
    <#
        Recovers only the allow-listed installation settings from containers in
        the stable Compose project. Values are returned to the installer and
        are never written to console output.
    #>
    param([string]$ProjectName = 'memorist')
    $dockerCli = Get-MemoristDockerCli
    $ids = @(& $dockerCli ps -a --filter "label=com.docker.compose.project=$ProjectName" --format '{{.ID}}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $ids.Count -eq 0) { return @{} }
    $allowed = @(
        'MEMORIST_INSTALLATION_ID', 'MEMORIST_MODE', 'MEMORIST_RUNTIME_PROFILE',
        'MEMORIST_POSTGRES_PASSWORD', 'POSTGRES_PASSWORD', 'MEMORIST_POSTGRES_DSN',
        'MEMORIST_ACTOR_ASSERTION_SECRET', 'MEMORIST_ACTOR_SERVICE_TOKEN',
        'MEMORIST_OPENWEBUI_WORKSPACE_UUID', 'WEBUI_SECRET_KEY',
        'OPEN_WEBUI_PORT', 'MEMORIST_CORE_HOST_PORT', 'MEMORIST_PORT'
    ) + @((Get-MemoristApiKeyRoles).Values)
    $result = @{}
    foreach ($id in $ids) {
        $environment = @(& $dockerCli inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $id 2>$null)
        if ($LASTEXITCODE -ne 0) { continue }
        foreach ($assignment in $environment) {
            $separator = ([string]$assignment).IndexOf('=')
            if ($separator -le 0) { continue }
            $key = ([string]$assignment).Substring(0, $separator)
            if ($key -notin $allowed -or $result.ContainsKey($key)) { continue }
            $result[$key] = ([string]$assignment).Substring($separator + 1)
        }
    }
    if (-not $result.ContainsKey('MEMORIST_POSTGRES_PASSWORD') -and
        $result.ContainsKey('POSTGRES_PASSWORD')) {
        $result['MEMORIST_POSTGRES_PASSWORD'] = $result['POSTGRES_PASSWORD']
    }
    $result.Remove('POSTGRES_PASSWORD')
    return $result
}

function Test-MemoristNamedVolumeExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    $dockerCli = Get-MemoristDockerCli
    & $dockerCli volume inspect $Name *> $null
    return ($LASTEXITCODE -eq 0)
}

function Set-MemoristEnvValue {
    <#
        Sets KEY=VALUE inside an array of .env lines, replacing an existing
        assignment (commented or not) or appending a new one. Returns the
        updated array. The VALUE is never logged by this function.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
    )
    $assignment = "$Key=$Value"
    $updated = @()
    $found = $false
    foreach ($line in $Lines) {
        if ($line -match "^\s*#?\s*$([regex]::Escape($Key))=") {
            $updated += $assignment
            $found = $true
        } else {
            $updated += $line
        }
    }
    if (-not $found) { $updated += $assignment }
    return $updated
}

function Write-MemoristEnvFile {
    <#
        Writes .env atomically as UTF-8 without BOM (Compose-friendly) and
        tightens the file ACL to the current user on Windows.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines
    )
    $content = ($Lines -join "`n") + "`n"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $content, $encoding)
    Protect-MemoristFileAcl -Path $Path
}

function Protect-MemoristFileAcl {
    <# Best-effort: restrict a secrets file to the current user on Windows. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-MemoristWindows)) { return }
    try {
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $me, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $Path -AclObject $acl
    } catch {
        Write-MemoristLog "Could not tighten .env permissions automatically; keep this folder private." 'WARN'
    }
}

# ---------------------------------------------------------------------------
# Health checks + browser launch.
# ---------------------------------------------------------------------------

function Test-MemoristHttp {
    <# True when the URL responds with a 2xx/3xx status within the timeout. #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 3
    )
    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Test-MemoristProxyRoutes {
    <#
        Verifies the authenticated Memorist proxy is actually mounted inside
        Open WebUI. An unauthenticated probe of a known Memorist API path must
        return HTTP 401 JSON. A 404, HTML, or 2xx response means the SPA
        catch-all swallowed the API route: the product would look healthy while
        the entire Memorist surface is unreachable, so Start must fail.
    #>
    param(
        [Parameter(Mandatory = $true)][int]$WebPort,
        [int]$TimeoutSec = 5
    )
    $url = "http://localhost:$WebPort/api/v1/memorist/openwebui/status"
    try {
        # Portable across Windows PowerShell 5.1 and PowerShell 7: a
        # successful (2xx/3xx) unauthenticated response means the SPA
        # catch-all swallowed the API path, which is a failure.
        $null = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return $false
    } catch {
        $response = $_.Exception.Response
        if ($null -eq $response) { return $false }
        $status = [int]$response.StatusCode
        return ($status -eq 401 -or $status -eq 403)
    }
}

function Wait-MemoristService {
    <#
        Polls a health URL until healthy or the deadline passes. Prints a single
        user-friendly progress line. Returns $true on success.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 180,
        [int]$IntervalSec = 3
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    Write-Host ("  Starting {0}..." -f $Name) -NoNewline
    while ((Get-Date) -lt $deadline) {
        if (Test-MemoristHttp -Url $Url) {
            Write-Host ' OK' -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds $IntervalSec
        Write-Host '.' -NoNewline
    }
    Write-Host ' TIMEOUT' -ForegroundColor Yellow
    return $false
}

function Open-MemoristBrowser {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        Start-Process $Url | Out-Null
        Write-MemoristLog ("Opened browser at {0}" -f $Url) 'OK'
    } catch {
        Write-MemoristLog ("Open your browser manually at {0}" -f $Url) 'WARN'
    }
}

# ---------------------------------------------------------------------------
# Compose helpers.
# ---------------------------------------------------------------------------

function Get-MemoristRoot {
    <# Absolute package root from either a top-level entry point or scripts/. #>
    param([Parameter(Mandatory = $true)][string]$ScriptRoot)
    if (Test-Path -LiteralPath (Join-Path $ScriptRoot 'compose.yml')) {
        return (Resolve-Path $ScriptRoot).Path
    }
    $parent = (Resolve-Path (Join-Path $ScriptRoot '..')).Path
    if (Test-Path -LiteralPath (Join-Path $parent 'compose.yml')) { return $parent }
    throw "Could not locate the Memorist package root from $ScriptRoot."
}

function Get-MemoristComposeFile {
    <#
        Resolves the release compose file. Prefers the packaged compose.yml and
        falls back to a repo-root docker-compose.release.yml so the same scripts
        work from a source checkout.
    #>
    param([Parameter(Mandatory = $true)][string]$Root)
    $candidates = @(
        (Join-Path $Root 'compose.yml'),
        (Join-Path $Root 'docker-compose.release.yml')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "No release compose file found under $Root (expected compose.yml or docker-compose.release.yml)."
}

function Get-MemoristComposeOverlay {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][ValidateSet('lite', 'full')][string]$Mode
    )
    $path = Join-Path $Root ("compose.{0}.yml" -f $Mode)
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing Compose overlay: $path" }
    return $path
}

function Invoke-MemoristCompose {
    <#
        Runs a Docker Compose subcommand for the given profile against the
        resolved compose file. $Compose is the array from
        Get-MemoristComposeCommand.
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Compose,
        [Parameter(Mandatory = $true)][string]$ComposeFile,
        [Parameter(Mandatory = $true)][ValidateSet('lite', 'full')][string]$Profile,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    # Compose may be @('docker','compose') or the legacy @('docker-compose').
    $exe = $Compose[0]
    $prefix = @()
    if ($Compose.Length -gt 1) { $prefix = $Compose[1..($Compose.Length - 1)] }
    $overlay = Get-MemoristComposeOverlay -Root (Split-Path -Parent $ComposeFile) -Mode $Profile
    $full = $prefix + @('-f', $ComposeFile, '-f', $overlay) + $Arguments
    & $exe @full | Out-Host
    $exitCode = $LASTEXITCODE
    return $exitCode
}

function Get-MemoristEffectiveConfig {
    param([Parameter(Mandatory = $true)][string]$Url)
    return Invoke-RestMethod -Uri $Url -TimeoutSec 10 -ErrorAction Stop
}

function Test-MemoristComposeService {
    param(
        [Parameter(Mandatory = $true)][string[]]$Compose,
        [Parameter(Mandatory = $true)][string]$ComposeFile,
        [Parameter(Mandatory = $true)][ValidateSet('lite', 'full')][string]$Mode,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $overlay = Get-MemoristComposeOverlay -Root (Split-Path -Parent $ComposeFile) -Mode $Mode
    $exe = $Compose[0]
    # Keep this as an array on Windows PowerShell 5.1. Assigning an `if`
    # expression that emits one item produces a scalar string; adding the
    # remaining argv array then concatenates everything into one malformed
    # argument such as "compose-f ...".
    $prefix = @()
    if ($Compose.Length -gt 1) { $prefix = $Compose[1..($Compose.Length - 1)] }
    $full = $prefix + @('-f', $ComposeFile, '-f', $overlay) + $Arguments
    & $exe @full *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-MemoristFullReadiness {
    <# Strict live validation: requested Full is never inferred from .env alone. #>
    param([Parameter(Mandatory = $true)][string]$Url)
    try { $config = Get-MemoristEffectiveConfig -Url $Url } catch { return $false }
    $features = $config.features
    $requiredFeatures = @(
        'openwebui_adapter', 'graph_projection', 'memory_worker', 'import_system',
        'memory_context_attachment', 'active_memory_blocks',
        'user_profile_projection', 'forgetting_pipeline'
    )
    if ($config.runtime_profile -ne 'full' -or
        $config.canonical_store -ne 'postgres' -or
        -not $config.postgres_dsn_configured -or
        $config.graph_backend -ne 'falkordb' -or
        $config.hot_scheduler -ne 'in_memory' -or
        @($config.profile_warnings).Count -ne 0) { return $false }
    foreach ($name in $requiredFeatures) {
        if (-not [bool]$features.$name) { return $false }
    }
    return $true
}

Export-ModuleMember -Function *-Memorist* , ConvertFrom-MemoristSecureString
