#!/usr/bin/env python3
"""Pre-export OpenCLIP models to the ONNX assets Argus's tagging engine expects.

Argus now exports CLIP automatically the first time you download a model in the UI, so
you usually do NOT need this script. It remains useful for pre-seeding the models/clip
volume ahead of time, or for producing assets to host behind ARGUS_CLIP_ASSET_BASE for
air-gapped / torch-free deployments.

    pip install torch open_clip_torch       # already in requirements.txt
    python scripts/export_clip.py --out models/clip            # both seeded models
    python scripts/export_clip.py --out models/clip --model ViT-B-32

Produces, per model, under <out>/<argus_name>/:
    image_encoder.onnx, text_encoder.onnx, bpe_simple_vocab_16e6.txt.gz

Run from the repo root so `app` is importable (or set PYTHONPATH=.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.clip_export import MODELS, export_model  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Export OpenCLIP models to ONNX for Argus")
    ap.add_argument("--out", default="models/clip", help="output base dir (default models/clip)")
    ap.add_argument("--model", choices=sorted(MODELS), help="export a single model (default: all)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    names = [args.model] if args.model else list(MODELS)
    for name in names:
        print(f"[{name}] exporting -> {out_dir / name}")
        export_model(name, out_dir / name)
        print(f"[{name}] done")


if __name__ == "__main__":
    main()
