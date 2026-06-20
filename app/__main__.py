"""CLI entry point — enables `python -m app`."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

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

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Argus face & object recognition server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
