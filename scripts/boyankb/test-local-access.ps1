param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 3081,
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$')]
    [string]$ImageTag
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$name = 'boyankb-librechat-test'
$null = & (Join-Path $PSScriptRoot 'init-local.ps1') -Name $name -Port $Port
$context = Get-BoyanLocalContext -Name $name
$environmentPath = Join-Path $context.State 'app.env'
$environment = Get-Content -LiteralPath $environmentPath -Raw
$testValues = @{
    BOYANKB_TEST_INSTANCE = $name
    BOYANKB_KNOWLEDGE_AGENT_ID = 'agent_acceptance'
    LOGIN_MAX = '100'
    REGISTER_MAX = '50'
}
foreach ($key in $testValues.Keys) {
    $pattern = "(?m)^$key=[^\r\n]*"
    if ([regex]::IsMatch($environment, $pattern)) {
        $environment = [regex]::Replace($environment, $pattern, "$key=$($testValues[$key])")
    } else {
        $environment = $environment.TrimEnd() + "`n$key=$($testValues[$key])`n"
    }
}
Write-BoyanPrivateFile -Path $environmentPath -Content $environment
$configPath = Join-Path $context.State 'librechat.yaml'
$config = Get-Content -LiteralPath $configPath -Raw
$config = $config.Replace('https://api.deepseek.com', 'http://127.0.0.1:3099/v1')
Write-BoyanPrivateFile -Path $configPath -Content $config

if (-not $ImageTag) {
    $ImageTag = Get-BoyanImageTag -Context $context -CurrentRevision
}
Write-BoyanPrivateFile -Path $context.ImageTagFile -Content "$ImageTag`n"
& (Join-Path $PSScriptRoot 'start-local.ps1') -Name $name -NoBuild

$testScript = Join-Path $PSScriptRoot 'test-local-access.cjs'
Invoke-BoyanCompose -Context $context -DockerArguments @('cp', $testScript, 'app:/app/test-local-access.cjs')
$testError = $null
try {
    Invoke-BoyanCompose -Context $context -DockerArguments @('exec', '-T', 'app', 'node', '/app/test-local-access.cjs')
} catch {
    $testError = $_
} finally {
    foreach ($file in @('browser-smoke-accounts.json', 'access-integration-results.json')) {
        $destination = Join-Path $context.State $file
        & docker compose --project-name $context.Name --env-file $context.Environment --file $context.Compose cp "app:/app/data/$file" $destination
    }
}

if ($testError) {
    throw $testError
}
Write-Output '权限集成验证通过。'
