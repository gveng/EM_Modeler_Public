$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$versionLine = Select-String -Path (Join-Path $root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($null -eq $versionLine) { throw 'Could not read project version from pyproject.toml.' }
$version = $versionLine.Matches[0].Groups[1].Value
$bundleVersion = $version -replace 'b\d+$', '_Beta'
$spec = Join-Path $root "EM3D_Modeler_$bundleVersion.spec"
if (-not (Test-Path $spec)) { throw "Versioned PyInstaller spec not found: $spec. Run scripts/update_pyinstaller_spec.py first." }

pyinstaller --clean --noconfirm $spec

$bundleName = [IO.Path]::GetFileNameWithoutExtension($spec)
$bundle = Join-Path $root "dist\$bundleName"
$internalBin = Join-Path $bundle '_internal\bin'
$rootBin = Join-Path $bundle 'bin'
if (Test-Path $internalBin) {
    New-Item -ItemType Directory -Force -Path $rootBin | Out-Null
    Copy-Item -Path (Join-Path $internalBin '*') -Destination $rootBin -Recurse -Force
}

$exe = Join-Path $bundle "$bundleName.exe"
if (-not (Test-Path $exe)) { throw "Portable executable was not created: $exe" }
if (-not (Test-Path $rootBin)) { throw "Portable native bin directory was not created: $rootBin" }
Write-Output "Portable build ready: $bundle"
