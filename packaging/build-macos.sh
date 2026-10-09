#!/usr/bin/env bash
# Builds a standalone, unsigned macOS distributable of Argus (Argus.app).
# No Python installation required to run the result.
# Must run natively on macOS (PyInstaller has no cross-compile path).
#
# Prerequisite: a .venv already set up with requirements.txt installed.
#
# Usage:
#   packaging/build-macos.sh            # single-file exe inside Argus.app (default)
#   packaging/build-macos.sh onedir     # folder inside Argus.app instead
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-onefile}"
if [[ "$MODE" != "onefile" && "$MODE" != "onedir" ]]; then
  echo "Usage: $0 [onefile|onedir]" >&2
  exit 1
fi

if [[ ! -e .venv/bin/python ]]; then
  echo "No .venv found -- create one first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Generate packaging/icon.icns from the favicon SVG if not already present.
# Requires Pillow (already in .venv via requirements.txt) and cairosvg, or
# skip this and leave icon_path unset in the spec (PyInstaller falls back to
# a generic icon, cosmetic only).
if [[ ! -e packaging/icon.icns ]]; then
  echo "== Generating packaging/icon.icns =="
  # Use the tray_app icon generator to produce a PNG, then iconutil it.
  ICONSET=$(mktemp -d)/icon.iconset
  mkdir -p "$ICONSET"
  .venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from packaging.tray_app import _make_icon
for sz in (16, 32, 64, 128, 256, 512, 1024):
    _make_icon(sz).save(f"/tmp/argus-icon-{sz}.png")
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

echo "== Installing pyinstaller + tray dependencies =="
.venv/bin/pip install pyinstaller
.venv/bin/pip install -r packaging/tray_requirements.txt

echo "== Installing UPX (binary compressor) =="
brew install upx 2>/dev/null || true

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
