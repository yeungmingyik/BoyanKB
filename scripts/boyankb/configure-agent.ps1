param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,128}$')]
    [string]$AgentId,
    [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
    [string]$Name = 'boyankb-librechat',
    [switch]$NoRestart
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name $Name
$environmentPath = Join-Path $context.State 'app.env'
$environment = Get-Content -LiteralPath $environmentPath -Raw
$pattern = '(?m)^BOYANKB_KNOWLEDGE_AGENT_ID=[^\r\n]*'
if ([regex]::Matches($environment, $pattern).Count -ne 1) {
    throw 'BOYANKB_KNOWLEDGE_AGENT_ID unavailable.'
}

$environment = [regex]::Replace($environment, $pattern, "BOYANKB_KNOWLEDGE_AGENT_ID=$AgentId")
Write-BoyanPrivateFile -Path $environmentPath -Content $environment

if (-not $NoRestart) {
    Invoke-BoyanCompose -Context $context -DockerArguments @('up', '--detach', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '240', 'app')
}
Write-Output '知识 Agent 已设置。'
