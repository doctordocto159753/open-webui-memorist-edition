<#
Pester tests for the Memorist installer shared module. These run only where
PowerShell + Pester are available (see the PR5-F workflow); Linux CI without
PowerShell relies on installer/scripts/validate_installer.py instead.
#>
$modulePath = Join-Path (Join-Path $PSScriptRoot '..') 'scripts\MemoristCommon.psm1'
$packageRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Import-Module $modulePath -Force

Describe 'Secret generation and masking' {
    It 'generates a 64-char hex secret by default' {
        $s = New-MemoristSecret
        if ($s -notmatch '^[0-9a-f]{64}$') { throw 'secret format mismatch' }
    }
    It 'generates distinct secrets' {
        if ((New-MemoristSecret) -eq (New-MemoristSecret)) { throw 'secrets were not distinct' }
    }
    It 'masks all but the last four characters' {
        if ((Get-MemoristMaskedSecret 'sk-abcdef1234') -ne '****1234') { throw 'mask mismatch' }
    }
    It 'never reveals a short secret' {
        if ((Get-MemoristMaskedSecret 'ab') -ne '****') { throw 'short secret leaked' }
    }
    It 'reports (none) for empty input' {
        if ((Get-MemoristMaskedSecret '') -ne '(none)') { throw 'empty mask mismatch' }
    }
}

Describe 'Env editing' {
    It 'replaces an existing assignment' {
        $lines = @('FOO=old', 'BAR=keep')
        $out = Set-MemoristEnvValue -Lines $lines -Key 'FOO' -Value 'new'
        if ($out -notcontains 'FOO=new' -or $out -notcontains 'BAR=keep') { throw 'env replacement failed' }
        if (($out | Where-Object { $_ -like 'FOO=*' }).Count -ne 1) { throw 'duplicate env assignment' }
    }
    It 'uncomments and sets a commented assignment' {
        $lines = @('# FOO=placeholder')
        $out = Set-MemoristEnvValue -Lines $lines -Key 'FOO' -Value 'v'
        if ($out -notcontains 'FOO=v') { throw 'commented assignment not replaced' }
    }
    It 'appends a missing key' {
        $out = Set-MemoristEnvValue -Lines @('A=1') -Key 'B' -Value '2'
        if ($out -notcontains 'B=2') { throw 'missing key not appended' }
    }
}

Describe 'Processing-role secret map' {
    It 'exposes the expected role env-var names' {
        $roles = Get-MemoristApiKeyRoles
        if ($roles['memory_extraction'] -ne 'MEMORIST_MEMORY_EXTRACTION_API_KEY') { throw 'extraction role mismatch' }
        if ($roles['embedding'] -ne 'MEMORIST_EMBEDDING_API_KEY') { throw 'embedding role mismatch' }
        if ($roles['preflight'] -ne 'MEMORIST_PREFLIGHT_API_KEY') { throw 'preflight role mismatch' }
        if ($roles['block_compaction'] -ne 'MEMORIST_BLOCK_COMPACTION_API_KEY') { throw 'compaction role mismatch' }
        if ($roles.Keys.Count -ne 7) { throw 'role count mismatch' }
    }
}

Describe 'Ollama is opt-in' {
    It 'keeps unattended installs off and supports an explicit switch' {
        $ollamaPackageRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
        $text = Get-Content (Join-Path $ollamaPackageRoot 'Install-Memorist.ps1') -Raw
        foreach ($needle in @('[switch]$EnableOllama', "'ENABLE_OLLAMA_API'", "Ollama integration disabled")) {
            if ($text -notmatch [regex]::Escape($needle)) { throw "missing Ollama contract: $needle" }
        }
        $template = Get-Content (Join-Path $ollamaPackageRoot '.env.example') -Raw
        if ($template -notmatch 'ENABLE_OLLAMA_API=false') { throw 'Ollama must default off' }
        if ($template -notmatch 'OPENWEBUI_RAG_EMBEDDING_ENGINE=openai') {
            throw 'RAG embeddings must not trigger a local model download by default'
        }
    }
}

Describe 'Windows host-port allocation' {
    It 'parses language-independent excluded TCP ranges' {
        $ranges = @(Get-MemoristExcludedTcpPortRanges -NetshOutput @(
            'Start Port    End Port',
            '----------    --------',
            '      3000        3010',
            '     8777       8777     *'
        ))
        if ($ranges.Count -ne 2) { throw 'excluded ranges were not parsed' }
        if (-not (Test-MemoristPortExcluded -Port 3005 -ExcludedRanges $ranges)) {
            throw 'port inside excluded range was accepted'
        }
    }

    It 'selects the first non-active and non-excluded port deterministically' {
        $ranges = @([pscustomobject]@{ Start = 3000; End = 3001 })
        $selected = Get-MemoristAvailablePort -PreferredPort 3000 -ExcludedRanges $ranges -PortProbe {
            param($candidate)
            return $candidate -ne 3002
        }
        if ($selected -ne 3003) { throw "expected 3003, got $selected" }
    }

    It 'keeps 8777 when free and skips it when occupied or excluded' {
        $free = Get-MemoristAvailablePort -PreferredPort 8777 -PortProbe { param($candidate) $true }
        if ($free -ne 8777) { throw "expected free port 8777, got $free" }
        $occupied = Get-MemoristAvailablePort -PreferredPort 8777 -PortProbe {
            param($candidate)
            return $candidate -ne 8777
        }
        if ($occupied -ne 8778) { throw "expected occupied fallback 8778, got $occupied" }
        $excluded = Get-MemoristAvailablePort -PreferredPort 8777 -ExcludedRanges @(
            [pscustomobject]@{ Start = 8777; End = 8778 }
        ) -PortProbe { param($candidate) $true }
        if ($excluded -ne 8779) { throw "expected excluded fallback 8779, got $excluded" }
    }
}

Describe 'Installed mode authority' {
    BeforeEach {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ("memorist-mode-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $root | Out-Null
    }
    AfterEach { Remove-Item -LiteralPath $root -Recurse -Force }

    It 'reads Full from the installed env' {
        [IO.File]::WriteAllText((Join-Path $root '.env'), "MEMORIST_MODE=full`n")
        if ((Get-MemoristInstalledMode -Root $root) -ne 'full') { throw 'Full mode was not read' }
    }
    It 'fails closed for a missing env' {
        $threw = $false
        try { Get-MemoristInstalledMode -Root $root | Out-Null } catch { $threw = $true }
        if (-not $threw) { throw 'missing env did not fail closed' }
    }
    It 'fails closed for a corrupt mode' {
        [IO.File]::WriteAllText((Join-Path $root '.env'), "MEMORIST_MODE=preview`n")
        $threw = $false
        try { Get-MemoristInstalledMode -Root $root | Out-Null } catch { $threw = $true }
        if (-not $threw) { throw 'corrupt mode did not fail closed' }
    }
}

Describe 'Lifecycle failure semantics' {
    # Pester v5 runs the Describe body during discovery but It blocks during the
    # run phase, so a bare assignment here is $null inside the tests. Set it in
    # BeforeAll so every It sees the resolved package root.
    BeforeAll {
        $packageRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
    }

    It 'makes installer dry-run fail when Compose validation fails' {
        $text = Get-Content (Join-Path $packageRoot 'Install-Memorist.ps1') -Raw
        if ($text -notmatch "Compose configuration failed to validate[\s\S]*exit 1") {
            throw 'installer can report a successful dry-run after Compose failure'
        }
    }

    It 'makes stop fail when Compose down fails' {
        $text = Get-Content (Join-Path $packageRoot 'Stop-Memorist.ps1') -Raw
        if ($text -notmatch "Stop failed[\s\S]*exit 1") { throw 'stop does not fail closed' }
    }

    It 'does not delete local folders when Docker volume reset is unavailable' {
        $text = Get-Content (Join-Path $packageRoot 'Reset-Memorist-Data.ps1') -Raw
        if ($text -notmatch "Reset aborted before deleting local folders[\s\S]*exit 1") {
            throw 'reset can claim success while Docker volumes remain'
        }
    }

    It 'makes uninstall fail before local purge when Compose down fails' {
        $text = Get-Content (Join-Path $packageRoot 'Uninstall-Memorist.ps1') -Raw
        if ($text -notmatch "Docker removal failed[\s\S]*exit 1") {
            throw 'uninstall can report success after Compose failure'
        }
    }

    It 'propagates Stop and Start failures through Restart' {
        $text = Get-Content (Join-Path $packageRoot 'Restart-Memorist.ps1') -Raw
        if ($text -notmatch 'Stop-Memorist\.ps1[\s\S]*\$LASTEXITCODE -ne 0') {
            throw 'restart ignores Stop failure'
        }
        if ($text -notmatch 'Start-Memorist\.ps1[\s\S]*\$LASTEXITCODE -ne 0') {
            throw 'restart ignores Start failure'
        }
    }

    It 'checks every script result in the Windows Full smoke' {
        $text = Get-Content (Join-Path $packageRoot 'Test-Memorist-Full.ps1') -Raw
        if ($text -notmatch 'Invoke-MemoristCheckedScript' -or $text -notmatch '\$LASTEXITCODE -ne 0') {
            throw 'Windows Full smoke can continue after a lifecycle script failure'
        }
    }

    It 'checks Full graph and backing services during Start' {
        $text = Get-Content (Join-Path $packageRoot 'Start-Memorist.ps1') -Raw
        foreach ($needle in @('graph_status', 'pg_isready', 'redis-cli')) {
            if ($text -notmatch [regex]::Escape($needle)) { throw "Full Start does not verify $needle" }
        }
    }

    It 'preflights preserved PostgreSQL credentials without altering roles' {
        $text = Get-Content (Join-Path $packageRoot 'Install-Memorist.ps1') -Raw
        foreach ($needle in @('PGPASSWORD="$POSTGRES_PASSWORD"', 'psql -h 127.0.0.1', 'SELECT 1', 'does not match the preserved database volume')) {
            if ($text -notmatch [regex]::Escape($needle)) { throw "missing credential preflight: $needle" }
        }
        if ($text -match 'ALTER\s+ROLE') { throw 'installer must not mutate the PostgreSQL role' }
    }

    It 'does not suppress Lite doctor failures' {
        $text = Get-Content (Join-Path $packageRoot 'scripts/start-lite.sh') -Raw
        if ($text -match 'doctor\.sh" lite \|\| true') { throw 'Lite startup suppresses doctor failure' }
    }
}
