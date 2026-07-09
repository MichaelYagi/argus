"""Entry point: python -m app.inference

Runs the inference HTTP server as a standalone sidecar process.

  python -m app.inference                  # 0.0.0.0:8200
  python -m app.inference --port 9000      # custom port

Default port 8200 (main app defaults to 8100).

When INFERENCE_URL=http://localhost:8200 is set in the main app environment,
runner.py will route infer_faces/infer_objects calls to this process over HTTP
instead of running in-process. When INFERENCE_URL is absent (the default),
the main app runs everything in one process — python -m app is unaffected.
"""

from __future__ import annotations

import argparse

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8200


def main() -> None:
    parser = argparse.ArgumentParser(description="Argus inference server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: 8200)")
    args = parser.parse_args()

    import uvicorn

    from app.inference.server import app
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
