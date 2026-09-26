param(
    [ValidatePattern('^(?:answer-(?:school|adult|camp|hackathon)-0[1-5]|insufficient-0[1-5]|security-0[1-5]|conflict-01)$')]
    [string]$Case,
    [ValidateSet('DeepSeek', 'Anthropic-Compatible')]
    [string]$Endpoint
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

$qaContext = Get-BoyanLocalContext -Name 'boyankb-librechat-sync-test'
$qaContainer = 'boyankb-librechat-sync-test-app-1'
$qaRunId = [guid]::NewGuid().ToString('D')
$qaScript = Join-Path $PSScriptRoot 'test-knowledge-qa.cjs'
$qaFixture = Join-Path $PSScriptRoot 'knowledge-qa-fixtures.cjs'
$qaScriptHash = (Get-FileHash -LiteralPath $qaScript -Algorithm SHA256).Hash.ToLowerInvariant()
$qaFixtureHash = (Get-FileHash -LiteralPath $qaFixture -Algorithm SHA256).Hash.ToLowerInvariant()

function Get-BoyanQaRuntime {
    $qaInspection = & docker inspect --format '{"id":{{json .Id}},"imageReference":{{json .Config.Image}},"imageId":{{json .Image}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"running":{{json .State.Running}},"startedAt":{{json .State.StartedAt}},"mounts":{{json .Mounts}}}' $qaContainer
    if ($LASTEXITCODE -ne 0) {
        throw 'Knowledge QA container unavailable.'
    }
    $qaInstance = $qaInspection | ConvertFrom-Json -AsHashtable
    if ($qaInstance.project -ne $qaContext.Name -or -not $qaInstance.running) {
        throw 'Knowledge QA instance mismatch.'
    }
    $qaInstance
}

$qaRuntime = Get-BoyanQaRuntime
$qaCodeMounts = @($qaRuntime.mounts | Where-Object { $_.Destination -match '^/app(?:$|/(?:packages|api|client|node_modules)(?:/|$))' } | ForEach-Object { @{ destination = $_.Destination; readOnly = -not $_.RW } })
foreach ($qaFile in @($qaScript, $qaFixture)) {
    Invoke-BoyanCompose -Context $qaContext -DockerArguments @('cp', $qaFile, "app:/app/$([IO.Path]::GetFileName($qaFile))")
}
$qaArguments = @(
    'exec', '-T', '--user', 'node', '--workdir', '/app',
    '--env', "BOYANKB_QA_RUN_ID=$qaRunId",
    '--env', "BOYANKB_QA_CASE=$Case",
    '--env', "BOYANKB_QA_ENDPOINT=$Endpoint",
    'app', 'node', '/app/test-knowledge-qa.cjs'
)
$qaRunError = $null
$qaReportError = $null
$qaRunReportPath = Join-Path $qaContext.State "knowledge-qa-run-$qaRunId.json"
try {
    Invoke-BoyanCompose -Context $qaContext -DockerArguments $qaArguments
} catch {
    $qaRunError = $_
} finally {
    try {
        Invoke-BoyanCompose -Context $qaContext -DockerArguments @('cp', 'app:/app/data/knowledge-qa-integration-results.json', $qaRunReportPath)
    } catch {
        $qaReportError = $_
    }
}
if ($qaReportError) {
    throw 'Knowledge QA report unavailable.'
}
$qaReport = Get-Content -LiteralPath $qaRunReportPath -Raw | ConvertFrom-Json -AsHashtable
if ($qaReport.runId -ne $qaRunId) {
    throw 'Knowledge QA current report unavailable.'
}
$qaRuntimeStable = $false
try {
    $qaFinalRuntime = Get-BoyanQaRuntime
    $qaRuntimeStable = $qaFinalRuntime.id -eq $qaRuntime.id -and $qaFinalRuntime.imageId -eq $qaRuntime.imageId -and $qaFinalRuntime.startedAt -eq $qaRuntime.startedAt
} catch {
    $qaRuntimeStable = $false
}
$qaReport.metadata = @{
    imageReference = $qaRuntime.imageReference
    imageId = $qaRuntime.imageId
    validationTarget = $qaCodeMounts.Count -gt 0 ? 'mounted-development-candidate' : 'isolated-image'
    codeMounts = $qaCodeMounts
    runtimeStable = $qaRuntimeStable
    testScriptSha256 = $qaScriptHash
    fixtureSha256 = $qaFixtureHash
    wrapperSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
if (-not $qaRuntimeStable) {
    $qaReport.passed = $false
}
$qaReportContent = $qaReport | ConvertTo-Json -Depth 15
Write-BoyanPrivateFile -Path $qaRunReportPath -Content $qaReportContent
Write-BoyanPrivateFile -Path (Join-Path $qaContext.State 'knowledge-qa-integration-results.json') -Content $qaReportContent
if ($qaRunError) {
    throw $qaRunError
}
if (-not $qaRuntimeStable) {
    throw 'Knowledge QA runtime changed during verification.'
}
if (-not $qaReport.passed -or -not $qaReport.keyCleanup) {
    throw 'Knowledge QA verification failed.'
}
Write-Output '知识问答验证通过。'
