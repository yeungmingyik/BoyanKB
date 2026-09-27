param(
    [Parameter(Mandatory)]
    [ValidateSet('Prepare', 'Enable', 'Disable', 'Status')]
    [string]$Action,
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat',
    [string]$Origin,
    [string]$TokenFile
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name $Name
$accessDirectory = Join-Path $context.State 'access'
$planFile = Join-Path $accessDirectory 'plan.json'
$remoteFile = Join-Path $accessDirectory 'remote-config.json'
$credentialFile = Join-Path $accessDirectory 'tunnel-token'
$restoreFile = Join-Path $accessDirectory 'restore.json'
$appFile = Join-Path $context.State 'app.env'
$accessKeys = @('DOMAIN_CLIENT', 'DOMAIN_SERVER', 'SESSION_COOKIE_SECURE', 'TRUST_PROXY')
$policyKeys = @('ALLOW_REGISTRATION', 'ALLOW_SOCIAL_LOGIN', 'ALLOW_SOCIAL_REGISTRATION', 'ALLOW_SHARED_LINKS', 'ALLOW_SHARED_LINKS_PUBLIC')

function Get-AccessOrigin {
    param([string]$Value)

    if ($Value -notmatch '\Ahttps://(?<hostname>[a-z0-9.-]+)(?::443)?/?\z') { throw 'ACCESS_ORIGIN_INVALID' }
    $hostname = $Matches.hostname.ToLowerInvariant()
    if ($hostname.Length -gt 253 -or $hostname -notmatch '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$') { throw 'ACCESS_ORIGIN_INVALID' }
    if ($hostname -eq 'localhost' -or $hostname.EndsWith('.localhost')) { throw 'ACCESS_ORIGIN_INVALID' }
    "https://$hostname"
}

function Get-AccessFields {
    param([string]$Path, [string[]]$Keys)

    $content = [IO.File]::ReadAllText($Path)
    $fields = @{}
    foreach ($key in $Keys) {
        $found = [regex]::Matches($content, "(?m)^$([regex]::Escape($key))=([^\r\n]*)\r?$")
        if ($found.Count -gt 1) { throw 'ACCESS_ENV_DUPLICATE' }
        $fields[$key] = if ($found.Count -eq 1) { $found[0].Groups[1].Value } else { $null }
    }
    $fields
}

function Set-AccessFields {
    param([string]$Path, [hashtable]$Fields)

    $content = [IO.File]::ReadAllText($Path)
    foreach ($key in $Fields.Keys) {
        $pattern = "(?m)^$([regex]::Escape($key))=[^\r\n]*(?:\r?\n|$)"
        $value = $Fields[$key]
        $replacement = if ($null -eq $value) { '' } else { "$key=$value`n" }
        if ([regex]::IsMatch($content, $pattern)) {
            $content = [regex]::Replace($content, $pattern, [Text.RegularExpressions.MatchEvaluator]{ param($match) $replacement })
        } elseif ($null -ne $value) {
            if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $content += "`n" }
            $content += $replacement
        }
    }
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        Write-BoyanPrivateFile -Path $temporary -Content $content
        [IO.File]::Move($temporary, $Path, $true)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

function Assert-AccessPolicy {
    $fields = Get-AccessFields -Path $appFile -Keys $policyKeys
    foreach ($key in $policyKeys) {
        if ($fields[$key] -ne 'false') { throw 'ACCESS_ACCOUNT_POLICY_INVALID' }
    }
}

function Read-AccessToken {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'ACCESS_TOKEN_MISSING' }
    $value = [IO.File]::ReadAllText($Path).Trim()
    if ($value.Length -gt 4096 -or $value -notmatch '^[A-Za-z0-9+/]+={0,2}$') { throw 'ACCESS_TOKEN_INVALID' }
    try {
        $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value)) | ConvertFrom-Json -AsHashtable
        if ($decoded.a -notmatch '^[a-f0-9]{32}$' -or $decoded.t -notmatch '^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$' -or [Convert]::FromBase64String($decoded.s).Length -ne 32) { throw 'ACCESS_TOKEN_INVALID' }
    } catch { throw 'ACCESS_TOKEN_INVALID' }
    $value
}

function Invoke-AccessCompose {
    param([string[]]$Arguments)

    Invoke-BoyanCompose -Context $context -DockerArguments $Arguments *> $null
}

function Get-AccessContainer {
    param([string]$Service)

    $value = & docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$Name-$Service-1" 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $parts = $value.Trim().Split('|')
    if ($parts.Count -ne 3 -or $parts[0] -ne $Name) { throw 'ACCESS_CONTAINER_SCOPE_INVALID' }
    @{ running = $parts[1] -eq 'running'; healthy = $parts[2] -eq 'healthy' }
}

function Read-AccessPlan {
    if (-not (Test-Path -LiteralPath $planFile -PathType Leaf)) { throw 'ACCESS_PREPARE_REQUIRED' }
    $value = Get-Content -LiteralPath $planFile -Raw | ConvertFrom-Json -AsHashtable
    if ($value.schemaVersion -ne 1 -or $value.instance -ne $Name -or (Get-AccessOrigin -Value $value.origin) -ne $value.origin) { throw 'ACCESS_PLAN_INVALID' }
    $remote = Get-Content -LiteralPath $remoteFile -Raw | ConvertFrom-Json -AsHashtable
    $rules = $remote.config.ingress
    if ($rules.Count -ne 2 -or $rules[0].Count -ne 2 -or $rules[0].hostname -ne ([Uri]$value.origin).Host -or $rules[0].service -ne 'http://app:3080' -or $rules[1].Count -ne 1 -or $rules[1].service -ne 'http_status:404') { throw 'ACCESS_INGRESS_INVALID' }
    $value
}

function Save-AccessBackup {
    $directory = Join-Path $accessDirectory "backups/$([DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmss'))-$([guid]::NewGuid().ToString('N'))"
    $null = New-Item -ItemType Directory -Path $directory
    Set-BoyanPrivateDirectory -Path $directory
    Copy-Item -LiteralPath $appFile -Destination (Join-Path $directory 'app.env')
    Copy-Item -LiteralPath $context.Environment -Destination (Join-Path $directory '.env')
    $directory
}

function Restart-AccessApp {
    Invoke-AccessCompose -Arguments @('up', '--detach', '--no-build', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '180', 'app')
}

function Restore-AccessFields {
    param([hashtable]$Snapshot)

    Set-AccessFields -Path $appFile -Fields $Snapshot.app
    Set-AccessFields -Path $context.Environment -Fields $Snapshot.compose
}

if (-not (Test-Path -LiteralPath $context.Environment -PathType Leaf) -or -not (Test-Path -LiteralPath $appFile -PathType Leaf)) { throw 'ACCESS_INSTANCE_MISSING' }
if ($Action -ne 'Prepare' -and ($Origin -or $TokenFile)) { throw 'ACCESS_PREPARE_ARGUMENTS_ONLY' }
$accessLock = $null
try {
    if ($Action -ne 'Status') {
        if (-not (Test-Path -LiteralPath $accessDirectory)) { $null = New-Item -ItemType Directory -Path $accessDirectory }
        Set-BoyanPrivateDirectory -Path $accessDirectory
        try {
            $accessLock = [IO.File]::Open((Join-Path $accessDirectory 'configure.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch { throw 'ACCESS_CONFIGURATION_BUSY' }
    }
    $enabled = (Get-AccessFields -Path $context.Environment -Keys @('BOYANKB_TUNNEL_ENABLED')).BOYANKB_TUNNEL_ENABLED -eq '1'

    if ($Action -eq 'Status') {
        $plan = if (Test-Path -LiteralPath $planFile) { Read-AccessPlan } else { $null }
        $tunnel = Get-AccessContainer -Service 'tunnel'
        [pscustomobject]@{
            prepared = $null -ne $plan
            enabled = $enabled
            healthy = $null -ne $tunnel -and $tunnel.running -and $tunnel.healthy
            url = if ($enabled -and $plan) { $plan.origin } else { Get-BoyanLocalUrl -Context $context }
        } | ConvertTo-Json -Compress
        return
    }

    if ($Action -eq 'Prepare') {
        if ($enabled) { throw 'ACCESS_DISABLE_BEFORE_PREPARE' }
        $normalizedOrigin = Get-AccessOrigin -Value $Origin
        Assert-AccessPolicy
        $token = if ($TokenFile) { Read-AccessToken -Path $TokenFile } else { $null }
        if ($null -ne $token) { Write-BoyanPrivateFile -Path $credentialFile -Content "$token`n" }
        $plan = @{ schemaVersion = 1; instance = $Name; origin = $normalizedOrigin }
        $remote = @{ config = @{ ingress = @(@{ hostname = ([Uri]$normalizedOrigin).Host; service = 'http://app:3080' }, @{ service = 'http_status:404' }) } }
        Write-BoyanPrivateFile -Path $planFile -Content ($plan | ConvertTo-Json -Depth 5)
        Write-BoyanPrivateFile -Path $remoteFile -Content ($remote | ConvertTo-Json -Depth 5)
        [pscustomobject]@{ prepared = $true; enabled = $false; tokenReady = Test-Path -LiteralPath $credentialFile -PathType Leaf; url = $normalizedOrigin } | ConvertTo-Json -Compress
        return
    }

    if ($Action -eq 'Disable' -and -not $enabled) {
        [pscustomobject]@{ enabled = $false; url = Get-BoyanLocalUrl -Context $context } | ConvertTo-Json -Compress
        return
    }

    if ($Action -eq 'Enable') {
        Assert-AccessPolicy
        $appState = Get-AccessContainer -Service 'app'
        if ($null -eq $appState -or -not $appState.running -or -not $appState.healthy) { throw 'ACCESS_APP_NOT_HEALTHY' }
    }
    $plan = Read-AccessPlan
    $snapshot = @{ app = Get-AccessFields -Path $appFile -Keys $accessKeys; compose = Get-AccessFields -Path $context.Environment -Keys @('BOYANKB_TUNNEL_ENABLED') }

    if ($Action -eq 'Enable') {
        $null = Read-AccessToken -Path $credentialFile
        if ($enabled) {
            if ($snapshot.app.DOMAIN_CLIENT -ne $plan.origin -or $snapshot.app.DOMAIN_SERVER -ne $plan.origin -or $snapshot.app.SESSION_COOKIE_SECURE -ne 'true' -or $snapshot.app.TRUST_PROXY -ne '1') { throw 'ACCESS_ACTIVE_CONFIGURATION_INVALID' }
            Invoke-AccessCompose -Arguments @('up', '--detach', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'tunnel')
            [pscustomobject]@{ enabled = $true; healthy = $true; url = $plan.origin } | ConvertTo-Json -Compress
            return
        }
        $null = Save-AccessBackup
        Write-BoyanPrivateFile -Path $restoreFile -Content ($snapshot | ConvertTo-Json -Depth 5)
        try {
            Set-AccessFields -Path $appFile -Fields @{ DOMAIN_CLIENT = $plan.origin; DOMAIN_SERVER = $plan.origin; SESSION_COOKIE_SECURE = 'true'; TRUST_PROXY = '1' }
            Set-AccessFields -Path $context.Environment -Fields @{ BOYANKB_TUNNEL_ENABLED = '1' }
            Invoke-AccessCompose -Arguments @('config', '--quiet')
            Restart-AccessApp
            Invoke-AccessCompose -Arguments @('up', '--detach', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'tunnel')
        } catch {
            $tunnelStopped = $true
            try { Invoke-AccessCompose -Arguments @('stop', '--timeout', '30', 'tunnel') } catch { $tunnelStopped = $false }
            try {
                Restore-AccessFields -Snapshot $snapshot
                Restart-AccessApp
            } catch { throw 'ACCESS_ENABLE_FAILED_ROLLBACK_REQUIRED' }
            if (-not $tunnelStopped) { throw 'ACCESS_ENABLE_FAILED_ROLLBACK_REQUIRED' }
            throw 'ACCESS_ENABLE_FAILED_RESTORED'
        }
        [pscustomobject]@{ enabled = $true; healthy = $true; url = $plan.origin } | ConvertTo-Json -Compress
        return
    }

    if (-not (Test-Path -LiteralPath $restoreFile)) { throw 'ACCESS_RESTORE_MISSING' }
    $restore = Get-Content -LiteralPath $restoreFile -Raw | ConvertFrom-Json -AsHashtable
    if (@($restore.app.Keys | Where-Object { $_ -notin $accessKeys }).Count -gt 0 -or @($restore.compose.Keys | Where-Object { $_ -ne 'BOYANKB_TUNNEL_ENABLED' }).Count -gt 0 -or $restore.app.Count -ne $accessKeys.Count -or $restore.compose.Count -ne 1) { throw 'ACCESS_RESTORE_INVALID' }
    $null = Save-AccessBackup
    try {
        Invoke-AccessCompose -Arguments @('stop', '--timeout', '30', 'tunnel')
        Restore-AccessFields -Snapshot $restore
        Invoke-AccessCompose -Arguments @('config', '--quiet')
        Restart-AccessApp
    } catch {
        try {
            Restore-AccessFields -Snapshot $snapshot
            Restart-AccessApp
            Invoke-AccessCompose -Arguments @('up', '--detach', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'tunnel')
        } catch { throw 'ACCESS_DISABLE_FAILED_ROLLBACK_REQUIRED' }
        throw 'ACCESS_DISABLE_FAILED_RESTORED'
    }
    [pscustomobject]@{ enabled = $false; url = Get-BoyanLocalUrl -Context $context } | ConvertTo-Json -Compress
} finally {
    if ($null -ne $accessLock) { $accessLock.Dispose() }
}
