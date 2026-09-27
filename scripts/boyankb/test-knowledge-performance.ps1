param(
    [ValidateSet(5, 10, 20)]
    [int]$Users = 10,
    [ValidateRange(1, 5)]
    [int]$Rounds = 3,
    [ValidateSet('Stub', 'Real')]
    [string]$ModelMode = 'Stub',
    [ValidateSet('All', 'Qa')]
    [string]$Scenario = 'All'
)

. (Join-Path $PSScriptRoot 'local-common.ps1')

if ($ModelMode -eq 'Real' -and $Users -gt 10) {
    throw 'Real model verification is limited to 10 users and one request per user.'
}

$performanceContext = Get-BoyanLocalContext -Name 'boyankb-librechat-sync-test'
$performanceContainer = 'boyankb-librechat-sync-test-app-1'
$performanceRunId = [guid]::NewGuid().ToString('D')
$performanceScript = Join-Path $PSScriptRoot 'test-knowledge-performance.cjs'
$performanceReportPath = Join-Path $performanceContext.State "knowledge-performance-$($ModelMode.ToLowerInvariant())-$Users-$performanceRunId.json"

function Get-BoyanPerformanceRuntime {
    $performanceRaw = & docker inspect --format '{{json .}}' $performanceContainer
    if ($LASTEXITCODE -ne 0) {
        throw 'Performance test container unavailable.'
    }
    $performanceInstance = $performanceRaw | ConvertFrom-Json -AsHashtable
    if ($performanceInstance.Config.Labels['com.docker.compose.project'] -ne $performanceContext.Name -or -not $performanceInstance.State.Running) {
        throw 'Performance test instance mismatch.'
    }
    foreach ($performanceRequired in @('NODE_ENV=test', 'BOYANKB_TEST_INSTANCE=boyankb-librechat-sync-test', 'BOYANKB_KNOWLEDGE_AGENT_ID=agent_sync_acceptance', 'FEISHU_APP_ID=fixture_app', 'FEISHU_APP_SECRET=fixture_secret')) {
        if ($performanceInstance.Config.Env -notcontains $performanceRequired) {
            throw 'Performance test environment mismatch.'
        }
    }
    $performanceInstance
}

$performanceRuntime = Get-BoyanPerformanceRuntime
$performanceCodeMounts = @($performanceRuntime.Mounts | Where-Object { $_.Destination -match '^/app(?:$|/(?:packages|api|client|node_modules)(?:/|$))' -and $_.Destination -notmatch '^/app/client/public/images(?:/|$)' } | ForEach-Object { @{ destination = $_.Destination; readOnly = -not $_.RW } })
$performanceContainerIds = @(& docker ps --filter "label=com.docker.compose.project=$($performanceContext.Name)" --format '{{.ID}}')
if ($LASTEXITCODE -ne 0 -or $performanceContainerIds.Count -lt 1) {
    throw 'Performance resource targets unavailable.'
}
Invoke-BoyanCompose -Context $performanceContext -DockerArguments @('cp', $performanceScript, 'app:/app/test-knowledge-performance.cjs')
$performanceSampler = Start-Job -ArgumentList (,$performanceContainerIds) -ScriptBlock {
    param([string[]]$ContainerIds)
    while ($true) {
        $sampleAt = [DateTime]::UtcNow.ToString('o')
        $sampleRows = & docker stats --no-stream --format '{{json .}}' @ContainerIds
        if ($LASTEXITCODE -eq 0) {
            foreach ($sampleRow in $sampleRows) {
                $sample = $sampleRow | ConvertFrom-Json -AsHashtable
                @{
                    at = $sampleAt
                    container = $sample.Name
                    cpu = $sample.CPUPerc
                    memory = $sample.MemUsage
                    memoryPercent = $sample.MemPerc
                    network = $sample.NetIO
                    disk = $sample.BlockIO
                    processes = $sample.PIDs
                }
            }
        }
        Start-Sleep -Seconds 2
    }
}
$performanceError = $null
$performanceSamples = @()
try {
    Invoke-BoyanCompose -Context $performanceContext -DockerArguments @(
        'exec', '-T', '--user', 'node', '--workdir', '/app',
        '--env', "BOYANKB_PERFORMANCE_RUN_ID=$performanceRunId",
        '--env', "BOYANKB_PERFORMANCE_USERS=$Users",
        '--env', "BOYANKB_PERFORMANCE_ROUNDS=$Rounds",
        '--env', "BOYANKB_PERFORMANCE_MODEL_MODE=$ModelMode",
        '--env', "BOYANKB_PERFORMANCE_SCENARIO=$Scenario",
        'app', 'node', '/app/test-knowledge-performance.cjs'
    )
} catch {
    $performanceError = $_
} finally {
    Stop-Job -Job $performanceSampler
    $performanceSamples = @(Receive-Job -Job $performanceSampler)
    Remove-Job -Job $performanceSampler
    Invoke-BoyanCompose -Context $performanceContext -DockerArguments @('cp', 'app:/app/data/knowledge-performance-results.json', $performanceReportPath)
}
$performanceReport = Get-Content -LiteralPath $performanceReportPath -Raw | ConvertFrom-Json -AsHashtable
if ($performanceReport.runId -ne $performanceRunId) {
    throw 'Current performance report unavailable.'
}
$performanceFinalRuntime = Get-BoyanPerformanceRuntime
$performanceStable = $performanceRuntime.Id -eq $performanceFinalRuntime.Id -and $performanceRuntime.Image -eq $performanceFinalRuntime.Image -and $performanceRuntime.State.StartedAt -eq $performanceFinalRuntime.State.StartedAt
$performanceReport.metadata = @{
    imageReference = $performanceRuntime.Config.Image
    imageId = $performanceRuntime.Image
    validationTarget = $performanceCodeMounts.Count -gt 0 ? 'mounted-development-candidate' : 'isolated-image'
    codeMounts = $performanceCodeMounts
    runtimeStable = $performanceStable
    testScriptSha256 = (Get-FileHash -LiteralPath $performanceScript -Algorithm SHA256).Hash.ToLowerInvariant()
    wrapperSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    hostOperatingSystem = [Runtime.InteropServices.RuntimeInformation]::OSDescription
    hostProcessorCount = [Environment]::ProcessorCount
    powershellVersion = $PSVersionTable.PSVersion.ToString()
}
$performanceReport.resourceSamples = @($performanceSamples | ForEach-Object {
    @{ at = $_.at; container = $_.container; cpu = $_.cpu; memory = $_.memory; memoryPercent = $_.memoryPercent; network = $_.network; disk = $_.disk; processes = $_.processes }
})
$performanceReport.resourceSampling = @{ source = 'docker stats'; intervalSeconds = 2; scope = 'isolated-test-project'; samples = $performanceSamples.Count }
if (-not $performanceStable -or $performanceSamples.Count -eq 0) {
    $performanceReport.passed = $false
}
Write-BoyanPrivateFile -Path $performanceReportPath -Content ($performanceReport | ConvertTo-Json -Depth 15)
if ($performanceError) {
    throw $performanceError
}
if (-not $performanceReport.passed -or -not $performanceReport.cleaned -or -not $performanceReport.sourceUnchanged) {
    throw 'Knowledge performance verification failed.'
}
Write-Output "知识性能验证通过：$Users 用户，$ModelMode。"
