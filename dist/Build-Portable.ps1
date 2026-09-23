param(
    [string]$PythonExe = 'python',
    [switch]$ReplaceExisting
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $root 'pyproject.toml'
$versionMatch = [regex]::Match(
    (Get-Content -Raw $projectFile),
    '(?m)^version\s*=\s*"([^"]+)"'
)
if (-not $versionMatch.Success) { throw "Project version not found in $projectFile" }

$version = $versionMatch.Groups[1].Value
$label = if ($version.Contains('b')) { "{0}_Beta" -f $version.Split('b')[0] } else { $version }
$bundleName = "EM3D_Modeler_$label"
$bundle = Join-Path $PSScriptRoot $bundleName
$spec = Join-Path $root "$bundleName.spec"
$generator = Join-Path $root 'scripts\update_pyinstaller_spec.py'
$icon = Join-Path $root 'Icons\SplashScreen\EM_Logo.ico'
$workPath = Join-Path (Join-Path $root 'Build') $bundleName

if (Test-Path $bundle) {
    if (-not $ReplaceExisting) {
        throw "Portable output already exists: $bundle. Use -ReplaceExisting to rebuild it."
    }
    Remove-Item -LiteralPath $bundle -Recurse -Force
}

if (-not (Test-Path $icon)) { throw "Application icon is missing: $icon" }

$pythonCommand = Get-Command -Name $PythonExe -ErrorAction Stop
$pythonPath = $pythonCommand.Source
$dependencyCheck = @'
import importlib.util
import sys
required = ('PyInstaller', 'emerge', 'emsutil', 'OCP', 'gmsh', 'pyvista', 'vtk', 'trame', 'scipy', 'numpy', 'matplotlib', 'PySide6')
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    print('Missing build/runtime packages: ' + ', '.join(missing), file=sys.stderr)
    raise SystemExit(1)
'@

Push-Location $root
try {
    & $pythonPath -c $dependencyCheck
    if ($LASTEXITCODE -ne 0) { throw 'The selected Python environment is missing one or more required build/runtime packages.' }

    & $pythonPath $generator
    if ($LASTEXITCODE -ne 0) { throw 'Failed to generate the versioned PyInstaller spec.' }
    if (-not (Test-Path $spec)) { throw "Generated spec not found: $spec" }

    & $pythonPath -m PyInstaller --clean --noconfirm --distpath $PSScriptRoot --workpath $workPath $spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to create the portable bundle.' }

    $exe = Join-Path $bundle "$bundleName.exe"
    $internal = Join-Path $bundle '_internal'
    if (-not (Test-Path $exe)) { throw "Portable executable was not created: $exe" }
    if (-not (Test-Path $internal)) { throw "PyInstaller runtime directory was not created: $internal" }

    $internalBin = Join-Path $internal 'bin'
    $portableBin = Join-Path $bundle 'bin'
    if (Test-Path $internalBin) {
        New-Item -ItemType Directory -Force -Path $portableBin | Out-Null
        Copy-Item -Path (Join-Path $internalBin '*') -Destination $portableBin -Recurse -Force
    }

    foreach ($relativePath in @(
        'docs\HELP.html',
        'Icons\SplashScreen\EM_Logo.ico',
        'Icons\SplashScreen\EM_Logo.png',
        'Icons\SplashScreen\EM_3d_MODELER_Splash_Screen.png'
    )) {
        if (-not (Test-Path (Join-Path $bundle $relativePath))) {
            throw "Required portable resource is missing: $relativePath"
        }
    }

    $qtPlatformPlugin = Get-ChildItem -Path $bundle -Filter 'qwindows.dll' -File -Recurse | Select-Object -First 1
    if ($null -eq $qtPlatformPlugin) { throw 'The Qt Windows platform plugin (qwindows.dll) is missing from the portable bundle.' }

    $bundleSize = (Get-ChildItem -Path $bundle -File -Recurse | Measure-Object -Property Length -Sum).Sum
    Write-Host "Portable distribution ready: $bundle" -ForegroundColor Green
    Write-Host "Executable: $exe"
    Write-Host ('Bundle size: {0:N2} GB' -f ($bundleSize / 1GB))
}
finally {
    Pop-Location
}