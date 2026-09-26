param(
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat',
    [ValidateRange(1024, 65535)]
    [int]$Port = 3080
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name $Name
$required = @('.env', 'app.env', 'mongo.env', 'librechat.yaml')

if (Test-Path -LiteralPath $context.State) {
    foreach ($file in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $context.State $file) -PathType Leaf)) {
            throw "Configuration incomplete: $($context.State)"
        }
    }
    Write-Output '配置已存在。'
    return
}

$staging = "$($context.State).init-$([Guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $staging -Force
Set-BoyanPrivateDirectory -Path $staging

$rootUser = "root_$(New-BoyanSecret -Bytes 8)"
$rootPassword = New-BoyanSecret
$appUser = "app_$(New-BoyanSecret -Bytes 8)"
$appPassword = New-BoyanSecret
$appEnvironment = Get-Content -LiteralPath (Join-Path $context.Repository 'deploy/boyankb/app.env.example') -Raw
$values = @{
    DOMAIN_CLIENT = "http://localhost:$Port"
    DOMAIN_SERVER = "http://localhost:$Port"
    MONGO_URI = "mongodb://${appUser}:${appPassword}@mongodb:27017/BoyanKB?authSource=BoyanKB"
    JWT_SECRET = New-BoyanSecret
    JWT_REFRESH_SECRET = New-BoyanSecret
    CREDS_KEY = New-BoyanSecret
    CREDS_IV = New-BoyanSecret -Bytes 16
    METRICS_SECRET = New-BoyanSecret
}

foreach ($key in $values.Keys) {
    $appEnvironment = [regex]::Replace($appEnvironment, "(?m)^$key=[^\r\n]*", "$key=$($values[$key])")
}

$composeEnvironment = @(
    "BOYANKB_STATE_DIR=$($context.State.Replace('\', '/'))"
    "BOYANKB_HTTP_PORT=$Port"
) -join "`n"
$mongoEnvironment = @(
    "MONGO_INITDB_ROOT_USERNAME=$rootUser"
    "MONGO_INITDB_ROOT_PASSWORD=$rootPassword"
    'MONGO_INITDB_DATABASE=BoyanKB'
    "MONGO_APP_USERNAME=$appUser"
    "MONGO_APP_PASSWORD=$appPassword"
) -join "`n"

Write-BoyanPrivateFile -Path (Join-Path $staging '.env') -Content "$composeEnvironment`n"
Write-BoyanPrivateFile -Path (Join-Path $staging 'app.env') -Content $appEnvironment
Write-BoyanPrivateFile -Path (Join-Path $staging 'mongo.env') -Content "$mongoEnvironment`n"
Copy-Item -LiteralPath (Join-Path $context.Repository 'deploy/boyankb/librechat.yaml') -Destination (Join-Path $staging 'librechat.yaml')

$localRoot = [IO.Path]::GetFullPath((Join-Path $context.Repository '.local'))
$resolvedStaging = [IO.Path]::GetFullPath($staging)
$resolvedState = [IO.Path]::GetFullPath($context.State)
if (-not $resolvedStaging.StartsWith("$localRoot$([IO.Path]::DirectorySeparatorChar)") -or -not $resolvedState.StartsWith("$localRoot$([IO.Path]::DirectorySeparatorChar)")) {
    throw 'Invalid state directory.'
}
Move-Item -LiteralPath $resolvedStaging -Destination $resolvedState
Write-Output '初始化完成。'
