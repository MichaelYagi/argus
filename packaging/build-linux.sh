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

# onnxruntime-gpu bundles CUDA/cuDNN shared libraries (~500 MB on Linux),
# bloating the frozen binary past GitHub's 2 GB release-asset limit.
# Linux runners have no CUDA anyway; swap in the CPU-only wheel instead.
# The app auto-detects GPU availability at runtime via
# onnxruntime.get_available_providers(), so behaviour is unchanged.
echo "== Swapping onnxruntime-gpu for CPU-only onnxruntime =="
.venv/bin/pip uninstall -y onnxruntime-gpu 2>/dev/null || true
.venv/bin/pip install onnxruntime

echo "== Installing UPX (binary compressor) =="
sudo apt-get install -y upx-ucl 2>/dev/null || true

echo "== Running PyInstaller =="
rm -rf build dist
.venv/bin/pyinstaller packaging/argus-linux.spec --distpath dist --workpath build

echo
echo "Built: dist/argus"
echo "Run it: ./dist/argus [--host 0.0.0.0] [--port 8100]"
