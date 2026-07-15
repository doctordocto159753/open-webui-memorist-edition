<#
Pester tests for the Memorist installer shared module. These run only where
PowerShell + Pester are available (see the PR5-D workflow); Linux CI without
PowerShell relies on installer/scripts/validate_installer.py instead.
#>
BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '..' 'scripts' 'MemoristCommon.psm1'
    Import-Module $modulePath -Force
}

Describe 'Secret generation and masking' {
    It 'generates a 64-char hex secret by default' {
        $s = New-MemoristSecret
        $s | Should -Match '^[0-9a-f]{64}$'
    }
    It 'generates distinct secrets' {
        (New-MemoristSecret) | Should -Not -Be (New-MemoristSecret)
    }
    It 'masks all but the last four characters' {
        Get-MemoristMaskedSecret 'sk-abcdef1234' | Should -Be '****1234'
    }
    It 'never reveals a short secret' {
        Get-MemoristMaskedSecret 'ab' | Should -Be '****'
    }
    It 'reports (none) for empty input' {
        Get-MemoristMaskedSecret '' | Should -Be '(none)'
    }
}

Describe 'Env editing' {
    It 'replaces an existing assignment' {
        $lines = @('FOO=old', 'BAR=keep')
        $out = Set-MemoristEnvValue -Lines $lines -Key 'FOO' -Value 'new'
        $out | Should -Contain 'FOO=new'
        $out | Should -Contain 'BAR=keep'
        ($out | Where-Object { $_ -like 'FOO=*' }).Count | Should -Be 1
    }
    It 'uncomments and sets a commented assignment' {
        $lines = @('# FOO=placeholder')
        $out = Set-MemoristEnvValue -Lines $lines -Key 'FOO' -Value 'v'
        $out | Should -Contain 'FOO=v'
    }
    It 'appends a missing key' {
        $out = Set-MemoristEnvValue -Lines @('A=1') -Key 'B' -Value '2'
        $out | Should -Contain 'B=2'
    }
}

Describe 'PR5-C role map' {
    It 'exposes the expected role env-var names' {
        $roles = Get-MemoristApiKeyRoles
        $roles['memory_extraction'] | Should -Be 'MEMORIST_MEMORY_EXTRACTION_API_KEY'
        $roles['embedding'] | Should -Be 'MEMORIST_EMBEDDING_API_KEY'
        $roles.Keys.Count | Should -Be 5
    }
}
