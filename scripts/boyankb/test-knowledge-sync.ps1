param()

. (Join-Path $PSScriptRoot 'local-common.ps1')

$context = Get-BoyanLocalContext -Name 'boyankb-librechat-sync-test'
$source = Join-Path $PSScriptRoot 'test-knowledge-sync.cjs'
Invoke-BoyanCompose -Context $context -DockerArguments @('cp', $source, 'app:/app/test-knowledge-sync.cjs')
$testError = $null
try {
    Invoke-BoyanCompose -Context $context -DockerArguments @('exec', '-T', 'app', 'node', '/app/test-knowledge-sync.cjs')
} catch {
    $testError = $_
} finally {
    foreach ($file in @('browser-smoke-accounts.json', 'knowledge-browser-fixture.json', 'knowledge-sync-integration-results.json')) {
        $destination = Join-Path $context.State $file
        & docker compose --project-name $context.Name --env-file $context.Environment --file $context.Compose cp "app:/app/data/$file" $destination
    }
}
if ($testError) {
    throw $testError
}
Write-Output '知识同步集成验证通过。'
