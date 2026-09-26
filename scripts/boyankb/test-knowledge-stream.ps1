param([switch]$IncludeSourceChanges)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$streamContext = Get-BoyanLocalContext -Name 'boyankb-librechat-sync-test'
$streamSource = Join-Path $PSScriptRoot 'test-knowledge-stream.cjs'
$streamContainer = 'boyankb-librechat-sync-test-app-1'
$streamInspection = & docker inspect --format '{{json .}}' $streamContainer
if ($LASTEXITCODE -ne 0) {
    throw 'Knowledge stream test container unavailable.'
}
$streamInstance = $streamInspection | ConvertFrom-Json -AsHashtable
if ($streamInstance.Config.Labels['com.docker.compose.project'] -ne $streamContext.Name -or -not $streamInstance.State.Running) {
    throw 'Knowledge stream test instance mismatch.'
}
$streamRequired = @('NODE_ENV=test', 'BOYANKB_TEST_INSTANCE=boyankb-librechat-sync-test', 'BOYANKB_KNOWLEDGE_AGENT_ID=agent_sync_acceptance', 'FEISHU_APP_ID=fixture_app', 'FEISHU_APP_SECRET=fixture_secret')
foreach ($streamValue in $streamRequired) {
    if ($streamInstance.Config.Env -notcontains $streamValue) {
        throw 'Knowledge stream test environment mismatch.'
    }
}
Invoke-BoyanCompose -Context $streamContext -DockerArguments @('cp', $streamSource, 'app:/app/test-knowledge-stream.cjs')
$streamArguments = @('exec', '-T', 'app', 'node', '/app/test-knowledge-stream.cjs')
if ($IncludeSourceChanges) {
    $streamArguments += '--source-changes'
}
$streamError = $null
try {
    Invoke-BoyanCompose -Context $streamContext -DockerArguments $streamArguments
} catch {
    $streamError = $_
} finally {
    $streamReportPath = Join-Path $streamContext.State 'knowledge-stream-integration-results.json'
    Invoke-BoyanCompose -Context $streamContext -DockerArguments @('cp', 'app:/app/data/knowledge-stream-integration-results.json', $streamReportPath)
}
$streamReport = Get-Content -LiteralPath $streamReportPath -Raw | ConvertFrom-Json -AsHashtable
$streamReport.metadata = @{
    imageReference = $streamInstance.Config.Image
    imageId = $streamInstance.Image
    validationTarget = 'isolated-synthetic'
    hasSourceOverrides = @($streamInstance.Mounts | Where-Object { $_.Destination -match '^/app/(api|packages|client)(/|$)' }).Count -gt 0
    sourceChanges = $IncludeSourceChanges.IsPresent
    testScriptSha256 = (Get-FileHash -LiteralPath $streamSource -Algorithm SHA256).Hash.ToLowerInvariant()
}
Write-BoyanPrivateFile -Path $streamReportPath -Content ($streamReport | ConvertTo-Json -Depth 10)
if ($streamError) {
    throw $streamError
}
if (-not $streamReport.passed -or -not $streamReport.cleaned) {
    throw 'Knowledge stream verification failed.'
}
Write-Output '知识流式权限验证通过。'
