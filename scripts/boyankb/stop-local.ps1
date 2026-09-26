param(
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat'
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name $Name
Invoke-BoyanCompose -Context $context -DockerArguments @('stop')
Write-Output '已停止。'
