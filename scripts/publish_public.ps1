param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,

    [string]$Branch = 'main',

    [string]$CommitMessage
)

$ErrorActionPreference = 'Stop'

function Invoke-Git {
    param(
        [string]$RepositoryPath,
        [string[]]$GitArguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & git -C $RepositoryPath @GitArguments 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($exitCode -ne 0) {
        throw "Git command failed (exit $exitCode): git $($GitArguments -join ' ')`n$($output -join "`n")"
    }

    if ($output) { Write-Output $output }
}

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gitRoot = (& git -C $sourceRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $gitRoot) {
    throw 'The project folder is not inside a Git repository.'
}
$gitRoot = (Resolve-Path $gitRoot.Trim()).Path
if ($gitRoot -ne $sourceRoot) {
    throw 'Run this script from the project repository root.'
}

$null = & git -C $sourceRoot diff --quiet HEAD -- 2>&1
if ($LASTEXITCODE -ne 0) {
    throw 'Commit tracked changes in the private repository before publishing. Only committed HEAD is published.'
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
    throw 'Branch cannot be empty.'
}

$projectVersionMatch = [regex]::Match(
    (Get-Content -Raw (Join-Path $sourceRoot 'pyproject.toml')),
    '(?m)^version\s*=\s*"([^"]+)"'
)
$packageVersionMatch = [regex]::Match(
    (Get-Content -Raw (Join-Path $sourceRoot 'src\em3d_modeler\__init__.py')),
    '(?m)^__version__\s*=\s*"([^"]+)"'
)
$readmeVersionMatch = [regex]::Match(
    (Get-Content -Raw (Join-Path $sourceRoot 'README.md')),
    '(?m)^Current version:\s*`([^`]+)`\s*$'
)
if (-not $projectVersionMatch.Success) {
    throw 'Could not read project version from pyproject.toml.'
}
if (
    -not $packageVersionMatch.Success -or
    -not $readmeVersionMatch.Success -or
    $packageVersionMatch.Groups[1].Value -ne $projectVersionMatch.Groups[1].Value -or
    $readmeVersionMatch.Groups[1].Value -ne $projectVersionMatch.Groups[1].Value
) {
    throw 'Version metadata in pyproject.toml, src/em3d_modeler/__init__.py, and README.md must match before publishing.'
}
$version = $projectVersionMatch.Groups[1].Value
if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
    $CommitMessage = "Publish version $version"
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('em3d-public-' + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $tempRoot 'snapshot.zip'
$publishRoot = Join-Path $tempRoot 'repository'
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    Invoke-Git $sourceRoot @('archive', '--format=zip', "--output=$archivePath", 'HEAD') | Out-Null
    Invoke-Git $sourceRoot @('clone', '--no-checkout', $RepositoryUrl, $publishRoot) | Out-Null

    & git -C $publishRoot show-ref --verify --quiet "refs/remotes/origin/$Branch" 2>$null
    $branchExists = $LASTEXITCODE -eq 0
    if (-not $branchExists -and $LASTEXITCODE -ne 1) {
        throw "Could not inspect target branch '$Branch'."
    }

    if ($branchExists) {
        Invoke-Git $publishRoot @('checkout', '-B', $Branch, "origin/$Branch") | Out-Null
    }
    else {
        Invoke-Git $publishRoot @('checkout', '--orphan', $Branch) | Out-Null
    }

    Get-ChildItem -LiteralPath $publishRoot -Force |
        Where-Object { $_.Name -ne '.git' } |
        Remove-Item -Recurse -Force
    Expand-Archive -LiteralPath $archivePath -DestinationPath $publishRoot -Force

    Invoke-Git $publishRoot @('add', '--all') | Out-Null
    & git -C $publishRoot diff --cached --quiet 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "Public repository is already up to date on '$Branch'."
        return
    }
    if ($LASTEXITCODE -ne 1) {
        throw 'Could not inspect changes in the public snapshot.'
    }

    foreach ($identityKey in @('user.name', 'user.email')) {
        $identityValue = (& git -C $sourceRoot config --get $identityKey 2>$null)
        if ($LASTEXITCODE -eq 0 -and $identityValue) {
            Invoke-Git $publishRoot @('config', $identityKey, $identityValue.Trim()) | Out-Null
        }
    }

    Invoke-Git $publishRoot @('commit', '-m', $CommitMessage) | Out-Null
    Invoke-Git $publishRoot @('push', 'origin', $Branch) | Out-Null
    Write-Output "Published committed snapshot to '$Branch' at $RepositoryUrl."
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}