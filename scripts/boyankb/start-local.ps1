param(
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat',
    [ValidateRange(1024, 65535)]
    [int]$Port = 3080,
    [switch]$NoBuild
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$null = & (Join-Path $PSScriptRoot 'init-local.ps1') -Name $Name -Port $Port
$context = Get-BoyanLocalContext -Name $Name
$imageTag = Get-BoyanImageTag -Context $context -CurrentRevision:(-not $NoBuild)
Invoke-BoyanCompose -Context $context -ImageTag $imageTag -DockerArguments @('config', '--quiet')
if (-not $NoBuild) {
    Invoke-BoyanCompose -Context $context -ImageTag $imageTag -DockerArguments @('build', 'app')
}
$null = & docker image inspect --format '{{.Id}}' "boyankb:$imageTag"
if ($LASTEXITCODE -ne 0) {
    throw "Image unavailable: boyankb:$imageTag"
}
Write-BoyanPrivateFile -Path $context.ImageTagFile -Content "$imageTag`n"
$services = @('app')
if ((Get-Content -LiteralPath $context.Environment -Raw) -match '(?m)^BOYANKB_SYNC_ENABLED=1\r?$') {
    $services += 'worker'
}
Invoke-BoyanCompose -Context $context -ImageTag $imageTag -DockerArguments (@('up', '--detach', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '600') + $services)
Write-Output (Get-BoyanLocalUrl -Context $context)
