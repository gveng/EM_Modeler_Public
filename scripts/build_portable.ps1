$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$spec = Get-ChildItem -Path $root -Filter 'EM3D_Modeler_*.spec' | Select-Object -First 1
if ($null -eq $spec) { throw 'No versioned PyInstaller spec found.' }

pyinstaller --clean --noconfirm $spec.FullName

$bundleName = [IO.Path]::GetFileNameWithoutExtension($spec.Name)
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
