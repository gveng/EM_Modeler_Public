param(
    [string]$PythonExe = 'python',
    [switch]$ReplaceExisting
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$portableBuilder = Join-Path $root 'dist\Build-Portable.ps1'
if (-not (Test-Path -LiteralPath $portableBuilder)) {
    throw "Portable build script not found: $portableBuilder"
}

if ($ReplaceExisting) {
    & $portableBuilder -PythonExe $PythonExe -ReplaceExisting
}
else {
    & $portableBuilder -PythonExe $PythonExe
}
