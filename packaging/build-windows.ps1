# Builds a standalone, unsigned Windows distributable of Argus.
# No Python installation required to run the result.
# Must run natively on Windows (PyInstaller has no cross-compile path).
#
# Prerequisite: a .venv already set up with requirements.txt installed.
# This script adds pyinstaller and the tray deps on top of that.
#
# Usage:
#   packaging\build-windows.ps1              # Argus.exe + _internal\ folder (default)
#   packaging\build-windows.ps1 -Mode onefile # single Argus.exe
# onedir is preferred: files live in dist\ permanently (no %TEMP% extraction),
# faster startup, and lower antivirus false-positive rate than onefile.
param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onedir"
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

Write-Host "== Installing UPX (binary compressor) =="
choco install upx -y --no-progress | Out-Null

Write-Host "== Generating packaging\icon.ico =="
if (-not (Test-Path "packaging\icon.ico")) {
    .\.venv\Scripts\python.exe - @"
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('tray_app', pathlib.Path('packaging/tray_app.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [mod._make_icon(s).convert('RGBA') for s in sizes]
frames[0].save('packaging/icon.ico', format='ICO', append_images=frames[1:],
               sizes=[(s, s) for s in sizes])
"@
}

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
