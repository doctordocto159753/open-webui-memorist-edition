<#
Pester tests for the Memorist installer shared module. These run only where
PowerShell + Pester are available (see the PR5-D workflow); Linux CI without
PowerShell relies on installer/scripts/validate_installer.py instead.
#>
$modulePath = Join-Path (Join-Path $PSScriptRoot '..') 'scripts\MemoristCommon.psm1'
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

Describe 'PR5-C role map' {
    It 'exposes the expected role env-var names' {
        $roles = Get-MemoristApiKeyRoles
        if ($roles['memory_extraction'] -ne 'MEMORIST_MEMORY_EXTRACTION_API_KEY') { throw 'extraction role mismatch' }
        if ($roles['embedding'] -ne 'MEMORIST_EMBEDDING_API_KEY') { throw 'embedding role mismatch' }
        if ($roles.Keys.Count -ne 5) { throw 'role count mismatch' }
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
