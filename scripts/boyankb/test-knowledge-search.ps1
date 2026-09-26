param()

. (Join-Path $PSScriptRoot 'local-common.ps1')

$searchContext = Get-BoyanLocalContext -Name 'boyankb-librechat-sync-test'
foreach ($searchFile in @('test-knowledge-search.cjs', 'knowledge-qa-fixtures.cjs')) {
    $searchSource = Join-Path $PSScriptRoot $searchFile
    Invoke-BoyanCompose -Context $searchContext -DockerArguments @('cp', $searchSource, "app:/app/$searchFile")
}
$searchError = $null
try {
    Invoke-BoyanCompose -Context $searchContext -DockerArguments @('exec', '-T', 'app', 'node', '/app/test-knowledge-search.cjs')
} catch {
    $searchError = $_
} finally {
    foreach ($searchFile in @('browser-smoke-accounts.json', 'qa-fixture.json', 'knowledge-search-integration-results.json')) {
        $searchDestination = Join-Path $searchContext.State $searchFile
        & docker compose --project-name $searchContext.Name --env-file $searchContext.Environment --file $searchContext.Compose cp "app:/app/data/$searchFile" $searchDestination
    }
}
if ($searchError) {
    throw $searchError
}
$searchImage = (& docker inspect --format '{{.Config.Image}}|{{.Image}}' boyankb-librechat-sync-test-app-1).Trim().Split('|')
if ($LASTEXITCODE -ne 0 -or $searchImage.Count -ne 2) {
    throw 'Knowledge search image unavailable.'
}
$searchMounts = (& docker inspect --format '{{json .Mounts}}' boyankb-librechat-sync-test-app-1) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw 'Knowledge search mounts unavailable.'
}
$searchCodeMounts = @($searchMounts | Where-Object { $_.Destination -match '^/app/(packages|api|client)(/|$)' } | ForEach-Object { @{ destination = $_.Destination; readOnly = -not $_.RW } })
$searchReportPath = Join-Path $searchContext.State 'knowledge-search-integration-results.json'
$searchReport = Get-Content -LiteralPath $searchReportPath -Raw | ConvertFrom-Json -AsHashtable
if (-not $searchReport.passed) {
    throw 'Knowledge search verification failed.'
}
$searchReport.metadata = @{
    imageReference = $searchImage[0]
    imageId = $searchImage[1]
    validationTarget = $searchCodeMounts.Count -gt 0 ? 'mounted-development-candidate' : 'isolated-image'
    codeMounts = $searchCodeMounts
    testScriptSha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'test-knowledge-search.cjs') -Algorithm SHA256).Hash.ToLowerInvariant()
}
Write-BoyanPrivateFile -Path $searchReportPath -Content ($searchReport | ConvertTo-Json -Depth 10)
Write-Output '知识搜索集成验证通过。'
