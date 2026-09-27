param()

. (Join-Path $PSScriptRoot 'local-common.ps1')

$testContext = Get-BoyanLocalContext -Name 'boyankb-librechat-local-common-test'
$testEnvironment = New-TemporaryFile
$testContext.Environment = $testEnvironment.FullName
[IO.File]::WriteAllText($testContext.Environment, "BOYANKB_SYNC_ENABLED=0`nBOYANKB_TUNNEL_ENABLED=0`n")
$testKeys = @('BOYANKB_IMAGE_TAG', 'BOYANKB_BUILD_COMMIT', 'BOYANKB_BUILD_BRANCH', 'BOYANKB_BUILD_DATE', 'BOYANKB_PRODUCT_VERSION')
$testPrevious = @{}
$checks = [Collections.Generic.List[object]]::new()

function Invoke-ComposeScenario {
    param([string]$Name, [string]$Branch, [int]$RevisionExit = 0, [int]$BranchExit = 0, [int]$DockerExit = 0)

    $script:composeMock = @{
        branch = $Branch
        revisionExit = $RevisionExit
        branchExit = $BranchExit
        dockerExit = $DockerExit
        calls = 0
        values = @{}
        arguments = @()
    }
    function git {
        if ($args[2] -eq 'rev-parse' -and $args[3] -eq 'HEAD') {
            $global:LASTEXITCODE = $script:composeMock.revisionExit
            if ($global:LASTEXITCODE -eq 0) { 'a' * 40 }
            return
        }
        if ($args[2] -eq 'branch' -and $args[3] -eq '--show-current') {
            $global:LASTEXITCODE = $script:composeMock.branchExit
            if ($global:LASTEXITCODE -eq 0 -and $script:composeMock.branch) { $script:composeMock.branch }
            return
        }
        throw 'LOCAL_COMMON_TEST_GIT_ARGUMENTS'
    }
    function docker {
        $script:composeMock.calls++
        $script:composeMock.arguments = @($args)
        foreach ($key in $testKeys) {
            $script:composeMock.values[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        }
        $global:LASTEXITCODE = $script:composeMock.dockerExit
        if ($global:LASTEXITCODE -eq 0) { 'compose-mock-output' }
    }
    $output = $null
    $failure = $null
    try {
        $output = Invoke-BoyanCompose -Context $testContext -ImageTag test -DockerArguments @('config', '--quiet')
    } catch {
        $failure = $_.Exception.Message
    }
    $expectedFailure = if ($RevisionExit) { 'Git revision unavailable.' } elseif ($BranchExit) { 'Git branch unavailable.' } elseif ($DockerExit) { "Docker Compose failed ($DockerExit)." } else { $null }
    $passed = $failure -eq $expectedFailure
    if ($RevisionExit -or $BranchExit) {
        $passed = $passed -and $script:composeMock.calls -eq 0
    } else {
        $expectedBranch = if ($Branch) { $Branch } else { 'detached' }
        $passed = $passed -and $script:composeMock.calls -eq 1 -and
            $script:composeMock.values['BOYANKB_BUILD_COMMIT'] -eq ('a' * 40) -and
            $script:composeMock.values['BOYANKB_BUILD_BRANCH'] -eq $expectedBranch -and
            $script:composeMock.values['BOYANKB_IMAGE_TAG'] -eq 'test' -and
            $script:composeMock.arguments[0] -eq 'compose' -and
            $script:composeMock.arguments[2] -eq $testContext.Name -and
            $script:composeMock.arguments[-2] -eq 'config' -and
            $script:composeMock.arguments[-1] -eq '--quiet'
        if (-not $DockerExit) { $passed = $passed -and $output -eq 'compose-mock-output' }
    }
    foreach ($key in $testKeys) {
        $expectedValue = if ($key -eq 'BOYANKB_BUILD_DATE') { $null } else { "before-$key" }
        $passed = $passed -and [Environment]::GetEnvironmentVariable($key, 'Process') -eq $expectedValue
    }
    $checks.Add(@{ name = $Name; passed = $passed; error = $failure; dockerCalls = $script:composeMock.calls })
    Remove-Variable -Name composeMock -Scope Script
}

try {
    foreach ($key in $testKeys) {
        $testPrevious[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($key -eq 'BOYANKB_BUILD_DATE') {
            Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($key, "before-$key", 'Process')
        }
    }
    Invoke-ComposeScenario -Name 'attached-branch' -Branch 'partner-access'
    Invoke-ComposeScenario -Name 'detached-head'
    Invoke-ComposeScenario -Name 'revision-command-failure' -RevisionExit 128
    Invoke-ComposeScenario -Name 'branch-command-failure' -BranchExit 128
    Invoke-ComposeScenario -Name 'compose-command-failure' -Branch 'partner-access' -DockerExit 23
} finally {
    foreach ($key in $testPrevious.Keys) {
        if ($null -eq $testPrevious[$key]) {
            Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($key, $testPrevious[$key], 'Process')
        }
    }
    Remove-Item -LiteralPath $testEnvironment.FullName -Force
}

$report = @{ passed = $checks.Count -eq 5 -and @($checks | Where-Object { -not $_.passed }).Count -eq 0; checks = $checks.ToArray(); runtime = 'mocked-git-and-compose' }
$report | ConvertTo-Json -Depth 5
if (-not $report.passed) { throw 'LOCAL_COMMON_TEST_FAILED' }
