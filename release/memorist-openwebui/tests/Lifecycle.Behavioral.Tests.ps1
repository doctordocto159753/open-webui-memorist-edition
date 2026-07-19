<#
PR5-G executable behavioral tests for the packaged lifecycle scripts.

Unlike Installer.Tests.ps1 (static contract checks, kept as supplementary
coverage), every test here RUNS the real script in an isolated sandbox with a
controlled `docker` shim on PATH, then asserts observable behavior: exit
codes, destructive ordering, filesystem effects, and the Start-time Memorist
proxy gate. No test talks to a real Docker daemon.
#>

BeforeAll {
    $script:packageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $script:isUnix = -not ($env:OS -eq 'Windows_NT')
    $script:powershellHost = if (Get-Command pwsh -ErrorAction SilentlyContinue) {
        'pwsh'
    } else {
        'powershell.exe'
    }

    function New-MemoristSandbox {
        param(
            [string]$Mode = 'lite',
            [int]$ComposeExit = 0,
            [int]$DockerInfoExit = 0,
            [switch]$NoEnv
        )
        $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("memorist behave " + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $sandbox | Out-Null
        foreach ($item in @(
                'Install-Memorist.ps1', 'Start-Memorist.ps1', 'Stop-Memorist.ps1',
                'Restart-Memorist.ps1', 'Reset-Memorist-Data.ps1', 'Uninstall-Memorist.ps1',
                'Show-Memorist-Logs.ps1', 'Test-Memorist-Full.ps1',
                'compose.yml', 'compose.lite.yml', 'compose.full.yml', '.env.example'
            )) {
            $source = Join-Path $script:packageRoot $item
            if (Test-Path -LiteralPath $source) { Copy-Item $source (Join-Path $sandbox $item) }
        }
        Copy-Item (Join-Path $script:packageRoot 'scripts') (Join-Path $sandbox 'scripts') -Recurse

        if (-not $NoEnv) {
            @(
                "MEMORIST_MODE=$Mode",
                'WEBUI_SECRET_KEY=test-secret',
                'MEMORIST_ACTOR_ASSERTION_SECRET=test-assert',
                'MEMORIST_ACTOR_SERVICE_TOKEN=test-token',
                'MEMORIST_OPENWEBUI_WORKSPACE_UUID=123e4567-e89b-42d3-a456-426614174000',
                'OPEN_WEBUI_PORT=3907',
                'MEMORIST_PORT=8907'
            ) | Set-Content -LiteralPath (Join-Path $sandbox '.env')
        }

        $shimDir = Join-Path $sandbox 'shim'
        New-Item -ItemType Directory -Path $shimDir | Out-Null
        $log = Join-Path $sandbox 'docker-invocations.log'
        if ($script:isUnix) {
            $shim = Join-Path $shimDir 'docker'
            $escapedLog = $log.Replace('\', '\\').Replace('"', '\"')
            $shimContent = @'
#!/bin/sh
echo "docker $@" >> "{0}"
case "$1" in
  info) exit {1} ;;
  compose)
    if [ "$2" = "version" ]; then exit 0; fi
    exit {2} ;;
esac
exit 0
'@ -f $escapedLog, $DockerInfoExit, $ComposeExit
            $shimContent | Set-Content -LiteralPath $shim -NoNewline
            chmod +x $shim
        } else {
            $shim = Join-Path $shimDir 'docker.cmd'
            @"
@echo off
echo docker %* >> "$log"
if "%1"=="info" exit /b $DockerInfoExit
if "%1"=="compose" (
  if "%2"=="version" exit /b 0
  exit /b $ComposeExit
)
exit /b 0
"@ | Set-Content -LiteralPath $shim
        }
        [pscustomobject]@{ Root = $sandbox; ShimDir = $shimDir; Shim = $shim; Log = $log }
    }

    function Invoke-MemoristScript {
        param(
            [Parameter(Mandatory = $true)]$Sandbox,
            [Parameter(Mandatory = $true)][string]$Script,
            [string[]]$Arguments = @()
        )
        # Inject the controlled docker shim deterministically via
        # MEMORIST_DOCKER_CLI (honored by MemoristCommon's Get-MemoristDockerCli)
        # instead of relying on PATH ordering, which is not reliable across
        # runners. The child PowerShell host inherits this process env var.
        $pwshArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Sandbox.Root $Script)) + $Arguments
        $previous = $env:MEMORIST_DOCKER_CLI
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $env:MEMORIST_DOCKER_CLI = $Sandbox.Shim
            # A deliberately failing lifecycle script writes a native stderr
            # record. Capture it as assertion input instead of letting the
            # Pester process's Stop preference abort this helper.
            $ErrorActionPreference = 'Continue'
            $output = & $script:powershellHost @pwshArgs 2>&1 | Out-String
            $exitCode = $LASTEXITCODE
            [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
            if ($null -eq $previous) {
                Remove-Item Env:MEMORIST_DOCKER_CLI -ErrorAction SilentlyContinue
            } else {
                $env:MEMORIST_DOCKER_CLI = $previous
            }
        }
    }

    function Start-MemoristStubServer {
        param(
            [Parameter(Mandatory = $true)][int]$WebPort,
            [Parameter(Mandatory = $true)][int]$CorePort,
            [Parameter(Mandatory = $true)][string]$ProxyBehavior # 'json401' | 'missing404'
        )
        $job = Start-Job -ScriptBlock {
            param($WebPort, $CorePort, $ProxyBehavior)
            $webListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $WebPort)
            $coreListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $CorePort)
            $listeners = @($webListener, $coreListener)
            foreach ($listener in $listeners) { $listener.Start() }
            while ($true) {
                foreach ($listener in $listeners) {
                    if (-not $listener.Pending()) { continue }
                    $client = $listener.AcceptTcpClient()
                    try {
                        $stream = $client.GetStream()
                        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::ASCII, $false, 1024, $true)
                        $requestLine = $reader.ReadLine()
                        while ($null -ne ($header = $reader.ReadLine()) -and $header -ne '') { }
                        $path = if ($requestLine) { $requestLine.Split(' ')[1] } else { '/' }
                        $status = '200 OK'
                        $contentType = 'application/json'
                        $body = '{"status":"ok"}'
                        if (
                            $listener.LocalEndpoint.Port -eq $WebPort -and
                            $path -eq '/api/v1/memorist/openwebui/status'
                        ) {
                            if ($ProxyBehavior -eq 'json401') {
                                $status = '401 Unauthorized'
                                $body = '{"detail":"authenticated Open WebUI actor required"}'
                            } else {
                                $status = '404 Not Found'
                                $contentType = 'text/html'
                                $body = '<html>SPA fallback</html>'
                            }
                        }
                        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
                        $head = "HTTP/1.1 $status`r`nContent-Type: $contentType`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n"
                        $headBytes = [Text.Encoding]::ASCII.GetBytes($head)
                        $stream.Write($headBytes, 0, $headBytes.Length)
                        $stream.Write($bytes, 0, $bytes.Length)
                        $stream.Flush()
                    } finally {
                        $client.Close()
                    }
                }
                Start-Sleep -Milliseconds 10
            }
        } -ArgumentList $WebPort, $CorePort, $ProxyBehavior
        Start-Sleep -Seconds 1
        return $job
    }
}

Describe 'Installed-mode authority (executable)' {
    It 'Start refuses an explicit -Mode that conflicts with the installed mode' {
        $sandbox = New-MemoristSandbox -Mode 'full'
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-Mode', 'lite', '-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match "configured for 'full' mode"
            $result.Output | Should -Match 'migration'
            # It must refuse BEFORE touching Docker at all.
            Test-Path $sandbox.Log | Should -BeFalse
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Start refuses lite-to-full bypass as well' {
        $sandbox = New-MemoristSandbox -Mode 'lite'
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-Mode', 'full', '-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match "configured for 'lite' mode"
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Restart refuses a conflicting -Mode before stopping anything' {
        $sandbox = New-MemoristSandbox -Mode 'full'
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Restart-Memorist.ps1' -Arguments @('-Mode', 'lite', '-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            Test-Path $sandbox.Log | Should -BeFalse
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Start without .env fails with installer guidance' {
        $sandbox = New-MemoristSandbox -NoEnv
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'Install-Memorist.ps1'
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }
}

Describe 'Compose failure semantics (executable)' {
    It 'keeps the Docker Compose v2 prefix as a separate argv item for service probes' {
        $sandbox = New-MemoristSandbox
        try {
            Import-Module (Join-Path $sandbox.Root 'scripts/MemoristCommon.psm1') -Force
            $ok = Test-MemoristComposeService `
                -Compose @($sandbox.Shim, 'compose') `
                -ComposeFile (Join-Path $sandbox.Root 'compose.yml') `
                -Mode 'lite' `
                -Arguments @('exec', '-T', 'memorist-core', 'true')

            $ok | Should -BeTrue
            $invocation = Get-Content -LiteralPath $sandbox.Log -Raw
            $invocation | Should -Match '^docker compose -f '
            $invocation | Should -Match 'exec -T memorist-core true'
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Stop exits non-zero when compose down fails' {
        $sandbox = New-MemoristSandbox -ComposeExit 7
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Stop-Memorist.ps1'
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'Stop failed'
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Restart propagates Stop failure and never reaches Start' {
        $sandbox = New-MemoristSandbox -ComposeExit 7
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Restart-Memorist.ps1' -Arguments @('-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            (Get-Content $sandbox.Log -Raw) | Should -Not -Match 'up'
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Reset preserves local data folders when volume removal fails' {
        $sandbox = New-MemoristSandbox -ComposeExit 3
        try {
            foreach ($dir in @('data', 'objects')) {
                New-Item -ItemType Directory -Path (Join-Path $sandbox.Root $dir) | Out-Null
                Set-Content -LiteralPath (Join-Path $sandbox.Root "$dir/keep.txt") -Value 'precious'
            }
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Reset-Memorist-Data.ps1' -Arguments @('-Force')
            $result.ExitCode | Should -Not -Be 0
            Test-Path (Join-Path $sandbox.Root 'data/keep.txt') | Should -BeTrue
            Test-Path (Join-Path $sandbox.Root 'objects/keep.txt') | Should -BeTrue
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Reset deletes local folders only after volume removal succeeds' {
        $sandbox = New-MemoristSandbox -ComposeExit 0
        try {
            New-Item -ItemType Directory -Path (Join-Path $sandbox.Root 'data') | Out-Null
            Set-Content -LiteralPath (Join-Path $sandbox.Root 'data/gone.txt') -Value 'x'
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Reset-Memorist-Data.ps1' -Arguments @('-Force')
            $result.ExitCode | Should -Be 0
            Test-Path (Join-Path $sandbox.Root 'data') | Should -BeFalse
            # .env (secrets) must survive a data reset without -IncludeEnv.
            Test-Path (Join-Path $sandbox.Root '.env') | Should -BeTrue
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Uninstall exits non-zero when compose removal fails' {
        $sandbox = New-MemoristSandbox -ComposeExit 5
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Uninstall-Memorist.ps1' -Arguments @('-Force')
            $result.ExitCode | Should -Not -Be 0
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }

    It 'Start fails when Docker daemon is unreachable' {
        $sandbox = New-MemoristSandbox -DockerInfoExit 1
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'Docker'
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }
}

Describe 'Installer DryRun (executable)' {
    It 'writes no .env and no secrets during a dry run' {
        $sandbox = New-MemoristSandbox -NoEnv
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Install-Memorist.ps1' -Arguments @('-Mode', 'lite', '-DryRun', '-NonInteractive', '-NoBrowser')
            Test-Path (Join-Path $sandbox.Root '.env') | Should -BeFalse
        } finally { Remove-Item $sandbox.Root -Recurse -Force }
    }
}

Describe 'Start-time Memorist proxy gate (executable)' {
    It 'fails Start when Open WebUI is healthy but Memorist proxy routes are absent' {
        $sandbox = New-MemoristSandbox -ComposeExit 0
        $stub = Start-MemoristStubServer -WebPort 3907 -CorePort 8907 -ProxyBehavior 'missing404'
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-NoBrowser')
            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'Memorist proxy routes'
        } finally {
            Stop-Job $stub -ErrorAction SilentlyContinue; Remove-Job $stub -Force -ErrorAction SilentlyContinue
            Remove-Item $sandbox.Root -Recurse -Force
        }
    }

    It 'passes Start when the proxy answers 401 JSON' {
        $sandbox = New-MemoristSandbox -ComposeExit 0
        $stub = Start-MemoristStubServer -WebPort 3907 -CorePort 8907 -ProxyBehavior 'json401'
        try {
            $result = Invoke-MemoristScript -Sandbox $sandbox -Script 'Start-Memorist.ps1' -Arguments @('-NoBrowser')
            $result.ExitCode | Should -Be 0
            $result.Output | Should -Match 'proxy routes verified'
        } finally {
            Stop-Job $stub -ErrorAction SilentlyContinue; Remove-Job $stub -Force -ErrorAction SilentlyContinue
            Remove-Item $sandbox.Root -Recurse -Force
        }
    }
}
