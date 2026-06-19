"""CLI entry point — enables `python -m app`."""

from __future__ import annotations

import argparse

# Load .env before app.main is imported so all env vars (SECRET_KEY, etc.)
# are in the environment when the FastAPI app and its middleware are created.
from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Argus face & object recognition server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
