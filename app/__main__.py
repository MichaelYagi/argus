"""CLI entry point — enables `python -m app`."""

from __future__ import annotations

import argparse
import faulthandler
import os
import secrets
from pathlib import Path

# Dump a native + Python traceback to stderr if the process crashes
# (segfaults from onnxruntime/faiss/CUDA are otherwise silent).
faulthandler.enable()

# macOS / Apple Silicon: onnxruntime (faces), torch/YOLO (objects), and faiss
# (matching) each bundle their own OpenMP runtime. KMP_DUPLICATE_LIB_OK stops the
# hard abort, but when these runtimes spin up competing thread pools they can
# segfault (e.g. an object detect right after a face detect). Pinning OpenMP/BLAS
# to a single thread keeps the runtimes from fighting. Must be set before any of
# numpy / faiss / onnxruntime / torch are imported.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
for _omp_var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_omp_var, "1")

from dotenv import load_dotenv  # noqa: E402

# Load .env before app.main is imported so all env vars are available
# when FastAPI and SessionMiddleware are initialised at import time.
load_dotenv()

# Auto-generate SECRET_KEY if not set.
# Persistence priority: .env file → data/.secret_key (survives Docker restarts).
if os.environ.get("SECRET_KEY", "change-me") == "change-me":
    # Try to load a previously generated key from the data volume first
    _secret_file = Path("data/.secret_key")
    if _secret_file.exists():
        _key = _secret_file.read_text().strip()
    else:
        _key = secrets.token_hex(32)
        # Persist: try .env, then data/.secret_key
        _env = Path(".env")
        try:
            if _env.exists():
                _text = _env.read_text()
                if "SECRET_KEY=" in _text:
                    _lines = [
                        f"SECRET_KEY={_key}" if ln.startswith("SECRET_KEY=") else ln
                        for ln in _text.splitlines()
                    ]
                    _env.write_text("\n".join(_lines) + "\n")
                else:
                    _env.write_text(_text.rstrip() + f"\nSECRET_KEY={_key}\n")
            else:
                _env.write_text(f"SECRET_KEY={_key}\n")
        except OSError:
            # .env not writable (e.g. read-only container) — fall back to data/
            try:
                _secret_file.parent.mkdir(parents=True, exist_ok=True)
                _secret_file.write_text(_key)
            except OSError:
                pass  # ephemeral key — sessions reset on restart

    os.environ["SECRET_KEY"] = _key

# Pre-load CUDA shared libraries from pip-installed nvidia packages.
# Setting LD_LIBRARY_PATH alone is unreliable on WSL2 — dlopen() may not
# re-read it after process start. ctypes.RTLD_GLOBAL injects the libraries
# into the global symbol table so onnxruntime-gpu finds them at import time.
import ctypes as _ctypes  # noqa: E402
import glob as _glob  # noqa: E402
import site as _site  # noqa: E402

_nvidia_lib_dirs: list[str] = []
for _sp in _site.getsitepackages():
    _nvidia = os.path.join(_sp, "nvidia")
    if os.path.isdir(_nvidia):
        for _pkg in os.listdir(_nvidia):
            _lib_dir = os.path.join(_nvidia, _pkg, "lib")
            if os.path.isdir(_lib_dir):
                _nvidia_lib_dirs.append(_lib_dir)

# Pre-load every versioned .so found in nvidia pip packages into the global
# symbol table so onnxruntime-gpu's dlopen() finds them regardless of
# LD_LIBRARY_PATH (unreliable in WSL2 after process start).
_SKIP_LIBS = {"libnvblas"}  # NVBLAS needs nvblas.conf — not required by onnxruntime

for _lib_dir in _nvidia_lib_dirs:
    for _so in sorted(_glob.glob(os.path.join(_lib_dir, "*.so.*"))):
        if os.path.isfile(_so) and not any(s in os.path.basename(_so) for s in _SKIP_LIBS):
            try:
                _ctypes.CDLL(_so, mode=_ctypes.RTLD_GLOBAL)
            except OSError:
                pass

if _nvidia_lib_dirs:
    _existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(_nvidia_lib_dirs) + (":" + _existing if _existing else "")

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Argus face & object recognition server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
