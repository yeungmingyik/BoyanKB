param(
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat',
    [ValidateSet('huggingface', 'modelscope')]
    [string]$ModelDownloadSource = 'huggingface',
    [switch]$NoBuild
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

& (Join-Path $PSScriptRoot 'start-local.ps1') -Name $Name -NoBuild:$NoBuild
$context = Get-BoyanLocalContext -Name $Name
$imageTag = Get-BoyanImageTag -Context $context
& docker run --rm --mount "type=bind,source=$($context.State),target=/config" "boyankb:$imageTag" node deploy/boyankb/configure-sync.cjs
if ($LASTEXITCODE -ne 0) {
    throw 'Synchronization configuration failed.'
}
$composeConfig = Get-Content -LiteralPath $context.Environment -Raw
$composeConfig = [regex]::Replace($composeConfig, '(?m)^BOYANKB_MODEL_DOWNLOAD_SOURCE=[^\r\n]*\r?\n?', '')
Write-BoyanPrivateFile -Path $context.Environment -Content "$($composeConfig.TrimEnd())`nBOYANKB_MODEL_DOWNLOAD_SOURCE=$ModelDownloadSource`n"
Invoke-BoyanCompose -Context $context -ImageTag $imageTag -DockerArguments @('config', '--quiet')
Invoke-BoyanCompose -Context $context -ImageTag $imageTag -DockerArguments @('up', '--detach', '--no-build', '--wait', '--wait-timeout', '600', 'app', 'worker')
Write-Output (Get-BoyanLocalUrl -Context $context)
