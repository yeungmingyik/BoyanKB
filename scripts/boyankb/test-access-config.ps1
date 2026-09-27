param()

. (Join-Path $PSScriptRoot 'local-common.ps1')

$testName = "boyankb-librechat-access-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$testContext = Get-BoyanLocalContext -Name $testName
$configurationScript = Join-Path $PSScriptRoot 'configure-access.ps1'
$null = & (Join-Path $PSScriptRoot 'init-local.ps1') -Name $testName -Port 3094
$testApp = Join-Path $testContext.State 'app.env'
$testAccess = Join-Path $testContext.State 'access'
$testTokenFile = Join-Path $testContext.State 'fixture-token'
$fixtureToken = @{ a = '0123456789abcdef0123456789abcdef'; t = '11111111-2222-3333-4444-555555555555'; s = [Convert]::ToBase64String([byte[]](1..32)) } | ConvertTo-Json -Compress
$fixtureToken = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fixtureToken))
Write-BoyanPrivateFile -Path $testTokenFile -Content $fixtureToken
$checks = [Collections.Generic.List[object]]::new()
$suiteComplete = $false

function Add-AccessCheck {
    param([string]$Name, [bool]$Passed)

    $checks.Add(@{ name = $Name; passed = $Passed })
    if (-not $Passed) { throw "ACCESS_TEST_$Name" }
}

function Read-TestFields {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $testApp) {
        if ($line -match '^([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
    }
    $values
}

function Invoke-MockedAccess {
    param([string]$Action, [string]$FailAt)

    $global:BoyanAccessMock = @{ name = $testName; calls = [Collections.Generic.List[object]]::new(); failAt = $FailAt; failed = $false }
    function docker {
        $arguments = @($args)
        $global:LASTEXITCODE = 0
        $global:BoyanAccessMock.calls.Add($arguments)
        if ($arguments[0] -eq 'inspect') {
            "$($global:BoyanAccessMock.name)|running|healthy"
            return
        }
        if ($arguments[0] -ne 'compose' -or $arguments[2] -ne $global:BoyanAccessMock.name) { throw 'ACCESS_TEST_SCOPE_INVALID' }
        if (-not $global:BoyanAccessMock.failed -and $arguments -contains 'up' -and $arguments[-1] -eq $global:BoyanAccessMock.failAt) {
            $global:BoyanAccessMock.failed = $true
            $global:LASTEXITCODE = 1
        }
    }
    try {
        $result = & $configurationScript -Name $testName -Action $Action
        @{ output = $result; error = $null; calls = $global:BoyanAccessMock.calls.ToArray() }
    } catch {
        @{ output = $null; error = $_.Exception.Message; calls = $global:BoyanAccessMock.calls.ToArray() }
    } finally {
        Remove-Variable -Name BoyanAccessMock -Scope Global
    }
}

try {
    $beforeApp = (Get-FileHash -LiteralPath $testApp).Hash
    $beforeCompose = (Get-FileHash -LiteralPath $testContext.Environment).Hash
    $prepare = & $configurationScript -Name $testName -Action Prepare -Origin 'https://KB.Example.test:443/' -TokenFile $testTokenFile | ConvertFrom-Json
    Add-AccessCheck -Name 'prepare-is-inactive' -Passed ($prepare.prepared -and -not $prepare.enabled -and $prepare.url -eq 'https://kb.example.test' -and (Get-FileHash -LiteralPath $testApp).Hash -eq $beforeApp -and (Get-FileHash -LiteralPath $testContext.Environment).Hash -eq $beforeCompose)
    $lock = [IO.File]::Open((Join-Path $testAccess 'configure.lock'), [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $busy = Invoke-MockedAccess -Action Enable
        Add-AccessCheck -Name 'concurrent-changes-rejected' -Passed ($busy.error -eq 'ACCESS_CONFIGURATION_BUSY' -and $busy.calls.Count -eq 0)
    } finally { $lock.Dispose() }
    foreach ($invalidOrigin in @('http://kb.example.test', 'https://user@kb.example.test', 'https://kb.example.test/path', 'https://kb.example.test/?query=1', 'https://kb.example.test/#fragment', 'https://127.0.0.1', 'https://localhost', 'https://kb.example.test:8443', 'https://kb.example.test/../', 'https://kb_example.test', 'https://*.example.test', "https://kb.example.test`n")) {
        $rejected = $false
        try { $null = & $configurationScript -Name $testName -Action Prepare -Origin $invalidOrigin } catch { $rejected = $_.Exception.Message -eq 'ACCESS_ORIGIN_INVALID' }
        Add-AccessCheck -Name "origin-$($checks.Count)" -Passed $rejected
    }
    $beforeFields = Read-TestFields
    $enabled = Invoke-MockedAccess -Action Enable
    $fields = Read-TestFields
    Add-AccessCheck -Name 'enable-secure-native-settings' -Passed ($null -eq $enabled.error -and $fields.DOMAIN_CLIENT -eq 'https://kb.example.test' -and $fields.DOMAIN_SERVER -eq 'https://kb.example.test' -and $fields.SESSION_COOKIE_SECURE -eq 'true' -and $fields.TRUST_PROXY -eq '1' -and $fields.ALLOW_REGISTRATION -eq 'false' -and $fields.ALLOW_SHARED_LINKS_PUBLIC -eq 'false')
    $composeQuiet = Invoke-BoyanCompose -Context $testContext -ImageTag local -DockerArguments @('config', '--quiet')
    $compose = Invoke-BoyanCompose -Context $testContext -ImageTag local -DockerArguments @('config', '--format', 'json') | ConvertFrom-Json -AsHashtable
    Add-AccessCheck -Name 'compose-network-isolation' -Passed ($compose.services.tunnel.networks.Count -eq 1 -and $compose.services.tunnel.networks.ContainsKey('partner-edge') -and $compose.services.app.networks.Count -eq 2 -and $compose.services.app.networks.ContainsKey('default') -and $compose.services.app.networks.ContainsKey('partner-edge') -and $compose.services.mongodb.networks.Count -eq 1 -and $compose.services.mongodb.networks.ContainsKey('default'))
    Add-AccessCheck -Name 'compose-private-ports-and-token' -Passed (-not $compose.services.tunnel.ContainsKey('ports') -and -not $compose.services.tunnel.ContainsKey('environment') -and $compose.services.app.ports[0].host_ip -eq '127.0.0.1' -and $compose.services.tunnel.command[-2] -eq '--token-file' -and $compose.services.tunnel.volumes[0].read_only -and $compose.services.tunnel.image -match '^cloudflare/cloudflared:2026\.9\.3@sha256:[a-f0-9]{64}$')
    Write-BoyanPrivateFile -Path (Join-Path $testContext.State 'rag.env') -Content "POSTGRES_DB=synthetic`nPOSTGRES_USER=synthetic`nPOSTGRES_PASSWORD=synthetic`n"
    [IO.File]::AppendAllText($testContext.Environment, "BOYANKB_SYNC_ENABLED=1`n")
    $syncedCompose = Invoke-BoyanCompose -Context $testContext -ImageTag local -DockerArguments @('config', '--format', 'json') | ConvertFrom-Json -AsHashtable
    $privateServices = @('worker', 'rag', 'vectordb', 'mongodb')
    $exposedServices = @($privateServices | Where-Object { $syncedCompose.services[$_].networks.Count -ne 1 -or -not $syncedCompose.services[$_].networks.ContainsKey('default') })
    Add-AccessCheck -Name 'sync-network-isolation' -Passed ($exposedServices.Count -eq 0 -and $syncedCompose.services.tunnel.networks.Count -eq 1 -and $syncedCompose.services.tunnel.networks.ContainsKey('partner-edge'))
    $activePrepareRejected = $false
    try { $null = & $configurationScript -Name $testName -Action Prepare -Origin 'https://other.example.test' } catch { $activePrepareRejected = $_.Exception.Message -eq 'ACCESS_DISABLE_BEFORE_PREPARE' }
    Add-AccessCheck -Name 'active-plan-protected' -Passed $activePrepareRejected
    [IO.File]::AppendAllText($testApp, "NEW_PROVIDER_API_KEY=synthetic-added-after-enable`n")
    $disabled = Invoke-MockedAccess -Action Disable
    $fields = Read-TestFields
    Add-AccessCheck -Name 'disable-restores-only-access' -Passed ($null -eq $disabled.error -and $fields.DOMAIN_CLIENT -eq $beforeFields.DOMAIN_CLIENT -and $fields.DOMAIN_SERVER -eq $beforeFields.DOMAIN_SERVER -and $fields.SESSION_COOKIE_SECURE -eq $beforeFields.SESSION_COOKIE_SECURE -and -not $fields.ContainsKey('TRUST_PROXY') -and $fields.NEW_PROVIDER_API_KEY -eq 'synthetic-added-after-enable' -and $fields.CREDS_KEY -eq $beforeFields.CREDS_KEY)
    $localCompose = Invoke-BoyanCompose -Context $testContext -ImageTag local -DockerArguments @('config', '--format', 'json') | ConvertFrom-Json -AsHashtable
    Add-AccessCheck -Name 'disabled-tunnel-excluded' -Passed (-not $localCompose.services.ContainsKey('tunnel') -and $localCompose.services.ContainsKey('worker') -and $localCompose.services.app.networks.Count -eq 1 -and $localCompose.services.app.networks.ContainsKey('default'))
    $failed = Invoke-MockedAccess -Action Enable -FailAt tunnel
    $fields = Read-TestFields
    Add-AccessCheck -Name 'enable-failure-restores-config' -Passed ($failed.error -eq 'ACCESS_ENABLE_FAILED_RESTORED' -and $fields.DOMAIN_CLIENT -eq $beforeFields.DOMAIN_CLIENT -and $fields.SESSION_COOKIE_SECURE -eq $beforeFields.SESSION_COOKIE_SECURE -and $fields.NEW_PROVIDER_API_KEY -eq 'synthetic-added-after-enable' -and (Get-Content -LiteralPath $testContext.Environment -Raw) -notmatch 'BOYANKB_TUNNEL_ENABLED=1')
    $null = Invoke-MockedAccess -Action Enable
    $disableFailed = Invoke-MockedAccess -Action Disable -FailAt app
    $fields = Read-TestFields
    Add-AccessCheck -Name 'disable-failure-restores-config' -Passed ($disableFailed.error -eq 'ACCESS_DISABLE_FAILED_RESTORED' -and $fields.DOMAIN_CLIENT -eq 'https://kb.example.test' -and $fields.SESSION_COOKIE_SECURE -eq 'true')
    $null = Invoke-MockedAccess -Action Disable
    $allCalls = @($enabled.calls) + @($disabled.calls) + @($failed.calls) + @($disableFailed.calls)
    Add-AccessCheck -Name 'token-never-in-arguments' -Passed (($allCalls | ConvertTo-Json -Depth 8) -notmatch [regex]::Escape($fixtureToken))
    $policyText = [IO.File]::ReadAllText($testApp).Replace('ALLOW_REGISTRATION=false', 'ALLOW_REGISTRATION=true')
    Write-BoyanPrivateFile -Path $testApp -Content $policyText
    $policyRejected = Invoke-MockedAccess -Action Enable
    Add-AccessCheck -Name 'open-registration-rejected' -Passed ($policyRejected.error -eq 'ACCESS_ACCOUNT_POLICY_INVALID')
    if ($IsWindows) {
        $acl = Get-Acl -LiteralPath $testAccess
        $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
        $unexpected = @($acl.Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed })
        Add-AccessCheck -Name 'private-access-acl' -Passed ($acl.AreAccessRulesProtected -and $unexpected.Count -eq 0)
    }
    $suiteComplete = $true
} finally {
    $report = @{ passed = $suiteComplete -and @($checks | Where-Object { -not $_.passed }).Count -eq 0; instance = $testName; checks = $checks.ToArray(); runtime = 'mocked-lifecycle-real-compose-validation'; at = [DateTimeOffset]::UtcNow.ToString('o') }
    Write-BoyanPrivateFile -Path (Join-Path $testContext.State 'access-config-results.json') -Content ($report | ConvertTo-Json -Depth 8)
}
Write-Output ($report | ConvertTo-Json -Depth 8)
