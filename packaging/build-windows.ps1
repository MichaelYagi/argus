# Builds a standalone, unsigned Windows distributable of Argus.
# No Python installation required to run the result.
# Must run natively on Windows (PyInstaller has no cross-compile path).
#
# Prerequisite: a .venv already set up with requirements.txt installed.
# This script adds pyinstaller and the tray deps on top of that.
#
# Usage:
#   packaging\build-windows.ps1              # single Argus.exe (default)
#   packaging\build-windows.ps1 -Mode onedir # folder instead -- faster startup
param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onefile"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "No .venv found." -ForegroundColor Red
    Write-Host "Create one first: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

Write-Host "== Installing pyinstaller + tray dependencies =="
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pip.exe install -r packaging\tray_requirements.txt

Write-Host "== Running PyInstaller ($Mode) =="
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
$env:PACKAGE_MODE = $Mode
.\.venv\Scripts\python.exe -m PyInstaller packaging\argus.spec --distpath dist --workpath build
$exitCode = $LASTEXITCODE
Remove-Item Env:\PACKAGE_MODE
if ($exitCode -ne 0) {
    Write-Host "PyInstaller build failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
if ($Mode -eq "onefile") {
    Write-Host "Built: dist\Argus.exe" -ForegroundColor Green
    Write-Host "Single file -- copy or zip just that, nothing else needed alongside it."
} else {
    Write-Host "Built: dist\Argus\" -ForegroundColor Green
    Write-Host "Zip that whole folder -- Argus.exe needs its _internal\ folder alongside it."
}
Write-Host "Unsigned: Windows SmartScreen will warn on first run -- click 'More info' -> 'Run anyway'."
