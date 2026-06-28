"""CLIP tagging engine: image + text encoders run in-process on onnxruntime.

Produces L2-normalized embeddings in a shared image/text space so an image vector can
be cosine-compared against vocabulary text vectors for zero-shot keyword tagging.

Assets (per model, under models/clip/<model_name>/):
  - image_encoder.onnx
  - text_encoder.onnx
  - bpe_simple_vocab_16e6.txt.gz   (shared CLIP BPE vocabulary)

No new pip dependency: onnxruntime, numpy and Pillow are already required; the BPE
tokenizer is vendored (app/core/clip_tokenizer.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.clip_tokenizer import CONTEXT_LENGTH, SimpleTokenizer

# CLIP image preprocessing constants (OpenAI/OpenCLIP).
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD = (0.26862954, 0.26130258, 0.27577711)
_INPUT_SIZE = 224


def _providers() -> list[str]:
    """onnxruntime providers (capability-selected: CUDA, CoreML, or CPU)."""
    from app.core import accelerator
    return accelerator.select_providers()


class TaggingEngine:
    def __init__(self, model_name: str, model_dir: Path) -> None:
        import onnxruntime as ort

        self._model_name = model_name
        self._dir = Path(model_dir)
        image_path = self._dir / "image_encoder.onnx"
        text_path = self._dir / "text_encoder.onnx"
        bpe_path = self._dir / "bpe_simple_vocab_16e6.txt.gz"
        for p in (image_path, text_path, bpe_path):
            if not p.exists():
                raise FileNotFoundError(f"CLIP asset missing: {p}")

        providers = _providers()
        self._img_sess = ort.InferenceSession(str(image_path), providers=providers)
        self._txt_sess = ort.InferenceSession(str(text_path), providers=providers)
        self._tokenizer = SimpleTokenizer(bpe_path)
        self._img_input = self._img_sess.get_inputs()[0].name
        self._txt_input = self._txt_sess.get_inputs()[0].name

    @property
    def model_name(self) -> str:
        return self._model_name

    # -- image side -----------------------------------------------------------

    def _preprocess(self, image: Any):
        import numpy as np
        from PIL import Image

        img = image.convert("RGB")
        # Resize shortest side to 224 then center-crop (CLIP preprocessing).
        w, h = img.size
        scale = _INPUT_SIZE / min(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.BICUBIC)
        w, h = img.size
        left = (w - _INPUT_SIZE) // 2
        top = (h - _INPUT_SIZE) // 2
        img = img.crop((left, top, left + _INPUT_SIZE, top + _INPUT_SIZE))

        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array(_MEAN, dtype=np.float32)) / np.array(_STD, dtype=np.float32)
        arr = arr.transpose(2, 0, 1)[None, ...]  # NCHW
        return arr.astype(np.float32)

    def embed_image(self, image: Any):
        """Return a 1-D L2-normalized float32 image vector."""
        import numpy as np

        arr = self._preprocess(image)
        out = self._img_sess.run(None, {self._img_input: arr})[0]
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    # -- text side ------------------------------------------------------------

    def embed_texts(self, words: list[str], batch_size: int = 256):
        """Return an (N, D) L2-normalized float32 matrix for the given words."""
        import numpy as np

        if not words:
            return np.zeros((0, 0), dtype=np.float32)
        rows: list[Any] = []
        for start in range(0, len(words), batch_size):
            chunk = words[start:start + batch_size]
            tokens = np.array(
                self._tokenizer.tokenize(chunk, CONTEXT_LENGTH), dtype=np.int64
            )
            out = self._txt_sess.run(None, {self._txt_input: tokens})[0]
            rows.append(np.asarray(out, dtype=np.float32))
        mat = np.concatenate(rows, axis=0)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms
