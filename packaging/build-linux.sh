#!/usr/bin/env bash
# Builds a standalone Linux x86_64 CLI binary of Argus.
# No Python installation required to run the result.
# Must run on Linux (PyInstaller has no cross-compile path).
# The resulting binary is tied to the glibc version of the build machine.
#
# Usage: packaging/build-linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -e .venv/bin/python ]]; then
  echo "No .venv found -- create one first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "== Installing pyinstaller =="
.venv/bin/pip install pyinstaller

echo "== Installing UPX (binary compressor) =="
sudo apt-get install -y upx-ucl 2>/dev/null || true

echo "== Running PyInstaller =="
rm -rf build dist
.venv/bin/pyinstaller packaging/argus-linux.spec --distpath dist --workpath build

echo
echo "Built: dist/argus"
echo "Run it: ./dist/argus [--host 0.0.0.0] [--port 8100]"
