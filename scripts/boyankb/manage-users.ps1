param(
    [Parameter(Mandatory)]
    [ValidateSet('create-user', 'invite-user', 'ban-user', 'reset-password', 'list-users')]
    [string]$Action,
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat'
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name $Name
Invoke-BoyanCompose -Context $context -DockerArguments @('exec', 'app', 'npm', 'run', $Action)
