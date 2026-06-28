"""One-time export of OpenCLIP checkpoints to the ONNX assets Argus runs.

torch + open_clip are heavy and are used ONLY here, to convert a CLIP checkpoint to
ONNX. Inference always runs on onnxruntime (see tagging_engine.py); torch never runs
at inference time and never touches the GPU. The import is done lazily inside
export_model so the cost is paid only when a CLIP model is actually exported (i.e. on
first download), not on every app start.

open_clip pulls the pretrained checkpoint from its own public host, so this is fully
self-service — the same model as YOLO/buffalo weights downloading themselves.
"""
from __future__ import annotations

from pathlib import Path

# Argus model name -> (open_clip arch, pretrained tag). Both seeded models use the
# 224px input and the same BPE vocabulary, so one tokenizer asset serves both.
MODELS = {
    "ViT-B-32": ("ViT-B-32", "laion2b_s34b_b79k"),
    "ViT-L-14": ("ViT-L-14", "laion2b_s32b_b82k"),
}

# The three files Argus's tagging engine loads, produced by export_model.
ASSET_FILES = ("image_encoder.onnx", "text_encoder.onnx", "bpe_simple_vocab_16e6.txt.gz")


def export_model(model_name: str, dest_dir: Path) -> None:
    """Export one OpenCLIP model to ONNX under dest_dir. Slow (downloads checkpoint)."""
    if model_name not in MODELS:
        raise ValueError(
            f"Unknown CLIP model '{model_name}'. Known: {', '.join(sorted(MODELS))}"
        )
    arch, pretrained = MODELS[model_name]

    import shutil

    import open_clip
    import torch

    model, _, _ = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
    model.eval()
    dest_dir.mkdir(parents=True, exist_ok=True)

    class ImageEncoder(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, image):
            return self.m.encode_image(image)

    class TextEncoder(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, tokens):
            return self.m.encode_text(tokens)

    img_dummy = torch.zeros(1, 3, 224, 224, dtype=torch.float32)
    txt_dummy = torch.zeros(1, 77, dtype=torch.int64)

    # Argus L2-normalizes encoder outputs itself, so the graphs return raw features.
    torch.onnx.export(
        ImageEncoder(model), img_dummy, str(dest_dir / "image_encoder.onnx"),
        input_names=["image"], output_names=["features"],
        dynamic_axes={"image": {0: "batch"}, "features": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    torch.onnx.export(
        TextEncoder(model), txt_dummy, str(dest_dir / "text_encoder.onnx"),
        input_names=["tokens"], output_names=["features"],
        dynamic_axes={"tokens": {0: "batch"}, "features": {0: "batch"}},
        opset_version=17, dynamo=False,
    )

    vocab_src = Path(open_clip.__file__).parent / "bpe_simple_vocab_16e6.txt.gz"
    if not vocab_src.exists():
        raise FileNotFoundError(f"open_clip BPE vocab not found at {vocab_src}")
    shutil.copyfile(vocab_src, dest_dir / "bpe_simple_vocab_16e6.txt.gz")
