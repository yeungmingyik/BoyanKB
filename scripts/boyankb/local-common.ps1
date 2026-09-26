if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'PowerShell 7+ required.'
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-BoyanLocalContext {
    param(
        [ValidatePattern('^boyankb-librechat(?:-[a-z0-9][a-z0-9-]{0,27})?$')]
        [string]$Name = 'boyankb-librechat'
    )

    $repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $state = Join-Path $repository ".local/$Name"

    [pscustomobject]@{
        Name = $Name
        Repository = $repository
        State = $state
        Environment = Join-Path $state '.env'
        ImageTagFile = Join-Path $state 'image-tag'
        Compose = Join-Path $repository 'deploy/boyankb/compose.yaml'
    }
}

function Get-BoyanImageTag {
    param([Parameter(Mandatory)]$Context, [switch]$CurrentRevision)

    if (-not $CurrentRevision -and (Test-Path -LiteralPath $Context.ImageTagFile -PathType Leaf)) {
        $imageTag = (Get-Content -LiteralPath $Context.ImageTagFile -Raw).Trim()
        if ($imageTag -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$') {
            throw 'Invalid image tag.'
        }
        return $imageTag
    }

    $version = (Get-Content -LiteralPath (Join-Path $Context.Repository 'VERSION') -Raw).Trim()
    $commit = & git -C $Context.Repository rev-parse --short=12 HEAD
    if ($LASTEXITCODE -ne 0) {
        throw 'Git revision unavailable.'
    }
    "$version-$($commit.Trim())"
}

function New-BoyanSecret {
    param([int]$Bytes = 32)

    [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes($Bytes)).ToLowerInvariant()
}

function Set-BoyanPrivateDirectory {
    param([string]$Path)

    if ($IsWindows) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $permissions = [Security.AccessControl.DirectorySecurity]::new()
        $permissions.SetAccessRuleProtection($true, $false)
        foreach ($principal in @($identity, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $principal,
                [Security.AccessControl.FileSystemRights]::FullControl,
                [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
                [Security.AccessControl.PropagationFlags]::None,
                [Security.AccessControl.AccessControlType]::Allow
            )
            $permissions.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $Path -AclObject $permissions
        return
    }

    [IO.File]::SetUnixFileMode($Path, [IO.UnixFileMode]'UserRead, UserWrite, UserExecute')
}

function Write-BoyanPrivateFile {
    param([string]$Path, [string]$Content)

    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
    if (-not $IsWindows) {
        [IO.File]::SetUnixFileMode($Path, [IO.UnixFileMode]'UserRead, UserWrite')
    }
}

function Invoke-BoyanCompose {
    param(
        [Parameter(Mandatory)]$Context,
        [Parameter(Mandatory)][string[]]$DockerArguments,
        [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$')]
        [string]$ImageTag
    )

    if (-not (Test-Path -LiteralPath $Context.Environment -PathType Leaf)) {
        throw 'Run init-local.ps1 first.'
    }

    $null = Get-Command docker -ErrorAction Stop
    $version = (Get-Content -LiteralPath (Join-Path $Context.Repository 'VERSION') -Raw).Trim()
    $commit = (& git -C $Context.Repository rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Git revision unavailable.'
    }
    $branch = (& git -C $Context.Repository branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Git branch unavailable.'
    }
    if (-not $ImageTag) {
        $ImageTag = Get-BoyanImageTag -Context $Context
    }
    $values = @{
        BOYANKB_IMAGE_TAG = $ImageTag
        BOYANKB_BUILD_COMMIT = $commit
        BOYANKB_BUILD_BRANCH = $branch
        BOYANKB_BUILD_DATE = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
        BOYANKB_PRODUCT_VERSION = $version
    }
    $previous = @{}

    try {
        foreach ($key in $values.Keys) {
            $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process')
        }

        & docker compose --project-name $Context.Name --env-file $Context.Environment --file $Context.Compose @DockerArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose failed ($LASTEXITCODE)."
        }
    } finally {
        foreach ($key in $previous.Keys) {
            if ($null -eq $previous[$key]) {
                Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
            }
        }
    }
}

function Get-BoyanLocalUrl {
    param([Parameter(Mandatory)]$Context)

    $portLines = @(Get-Content -LiteralPath $Context.Environment | Where-Object { $_ -match '^BOYANKB_HTTP_PORT=' })
    if ($portLines.Count -ne 1) {
        throw 'BOYANKB_HTTP_PORT unavailable.'
    }
    $port = $portLines[0].Substring('BOYANKB_HTTP_PORT='.Length)
    "http://localhost:$port"
}
