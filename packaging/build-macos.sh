#!/usr/bin/env bash
# Builds a standalone, unsigned macOS distributable of Argus (Argus.app).
# No Python installation required to run the result.
# Must run natively on macOS (PyInstaller has no cross-compile path).
#
# Prerequisite: a .venv already set up with requirements.txt installed.
#
# Usage:
#   packaging/build-macos.sh            # onedir bundle inside Argus.app (default)
#   packaging/build-macos.sh onefile    # single-file exe inside Argus.app
# onedir is preferred for long-running processes: files live permanently inside
# the .app bundle rather than being extracted to /var/folders/tmp on each launch,
# so macOS's temp-file cleaner cannot delete them while the app is running.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-onedir}"
if [[ "$MODE" != "onefile" && "$MODE" != "onedir" ]]; then
  echo "Usage: $0 [onefile|onedir]" >&2
  exit 1
fi

if [[ ! -e .venv/bin/python ]]; then
  echo "No .venv found -- create one first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "== Installing pyinstaller + tray dependencies =="
.venv/bin/pip install pyinstaller
.venv/bin/pip install -r packaging/tray_requirements.txt

echo "== Installing UPX (binary compressor) =="
brew install upx 2>/dev/null || true

# Generate packaging/icon.icns (requires Pillow + pystray, installed above).
if [[ ! -e packaging/icon.icns ]]; then
  echo "== Generating packaging/icon.icns =="
  ICONSET=$(mktemp -d)/icon.iconset
  mkdir -p "$ICONSET"
  .venv/bin/python - <<'PYEOF'
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("tray_app", pathlib.Path("packaging/tray_app.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for sz in (16, 32, 64, 128, 256, 512, 1024):
    mod._make_icon(sz).save(f"/tmp/argus-icon-{sz}.png")
PYEOF
  cp /tmp/argus-icon-16.png  "$ICONSET/icon_16x16.png"
  cp /tmp/argus-icon-32.png  "$ICONSET/icon_16x16@2x.png"
  cp /tmp/argus-icon-32.png  "$ICONSET/icon_32x32.png"
  cp /tmp/argus-icon-64.png  "$ICONSET/icon_32x32@2x.png"
  cp /tmp/argus-icon-128.png "$ICONSET/icon_128x128.png"
  cp /tmp/argus-icon-256.png "$ICONSET/icon_128x128@2x.png"
  cp /tmp/argus-icon-256.png "$ICONSET/icon_256x256.png"
  cp /tmp/argus-icon-512.png "$ICONSET/icon_256x256@2x.png"
  cp /tmp/argus-icon-512.png "$ICONSET/icon_512x512.png"
  cp /tmp/argus-icon-1024.png "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o packaging/icon.icns
  rm -rf "$(dirname "$ICONSET")"
fi

echo "== Running PyInstaller ($MODE) =="
rm -rf build dist
PACKAGE_MODE="$MODE" .venv/bin/pyinstaller packaging/argus.spec --distpath dist --workpath build

# The spec's BUNDLE step copies the exe/folder into dist/Argus.app and leaves
# the bare dist/Argus alongside it -- remove the bare one so only the .app remains.
if [[ -e "dist/Argus.app" ]]; then
  rm -rf "dist/Argus"
fi

echo
echo "Built: dist/Argus.app"
echo "Zip it (or Finder's Compress) to distribute."
echo "Unsigned: right-click -> Open -> Open (once) to bypass Gatekeeper the first time."
