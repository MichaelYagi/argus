"""FairFace ResNet-34 ONNX attribute predictor.

Predicts age group, gender, and ethnicity from a 224x224 face crop.
Used as a post-detection enrichment step in runner.py when face.use_fairface is enabled.

Model: https://github.com/yakhyo/fairface-onnx
Input:  (1, 3, 224, 224) float32, ImageNet-normalised RGB
Output: three tensors — race (7), gender (2), age (9) logits
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_URL = "https://github.com/yakhyo/fairface-onnx/releases/download/weights/fairface.onnx"

_RACE_LABELS   = ["White", "Black", "Latino_Hispanic", "East Asian", "Southeast Asian", "Indian", "Middle Eastern"]
_GENDER_LABELS = ["M", "F"]
_AGE_LABELS    = ["0-2", "3-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
_AGE_MIDPOINTS = [1,     6,     14,      24,      34,      44,      54,      64,      70]

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


class FairFaceEngine:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort
        from app.core import settings_cache

        providers = ["CPUExecutionProvider"]
        if settings_cache.cache.get_or("system.use_gpu", True):
            try:
                if "CUDAExecutionProvider" in ort.get_available_providers():
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            except Exception:
                pass

        t0 = time.monotonic()
        self._session = ort.InferenceSession(str(model_path), providers=providers)
        self._input_name   = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        logger.debug("FairFace loaded in %.1fs providers=%s", time.monotonic() - t0, providers)

    def analyze(self, crop_rgb: Any) -> dict | None:
        """Predict age, gender, ethnicity from an RGB face crop (numpy uint8 H×W×3).

        Returns {age: int, age_group: str, gender: 'M'|'F', ethnicity: str} or None on error.
        """
        try:
            blob = self._preprocess(crop_rgb)
            t0 = time.monotonic()
            outputs = self._session.run(self._output_names, {self._input_name: blob})
            logger.debug("FairFace inference %.0fms", (time.monotonic() - t0) * 1000)
            return self._decode(outputs)
        except Exception as exc:
            logger.debug("FairFace analyze error: %s", exc)
            return None

    # ------------------------------------------------------------------

    def _preprocess(self, crop_rgb: Any) -> np.ndarray:
        from PIL import Image
        arr = np.asarray(crop_rgb)
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise ValueError(f"expected H×W×3 array, got {arr.shape}")
        pil = Image.fromarray(arr.astype(np.uint8)).resize((224, 224), Image.BILINEAR)
        norm = (np.asarray(pil, dtype=np.float32) / 255.0 - _MEAN) / _STD
        return norm.transpose(2, 0, 1)[np.newaxis].astype(np.float32)   # (1,3,224,224)

    def _decode(self, outputs: list[np.ndarray]) -> dict:
        race_p   = _softmax(outputs[0].flatten())
        gender_p = _softmax(outputs[1].flatten())
        age_p    = _softmax(outputs[2].flatten())

        return {
            "age":       _AGE_MIDPOINTS[int(age_p.argmax())],
            "age_group": _AGE_LABELS[int(age_p.argmax())],
            "gender":    _GENDER_LABELS[int(gender_p.argmax())],
            "ethnicity": _RACE_LABELS[int(race_p.argmax())],
        }


def download_model(model_path: Path) -> None:
    """Download fairface.onnx to model_path. Blocks until complete or raises on failure."""
    import httpx

    model_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = model_path.with_suffix(".tmp")
    logger.info("Downloading FairFace model from %s …", _MODEL_URL)
    t0 = time.monotonic()
    try:
        with httpx.stream("GET", _MODEL_URL, timeout=httpx.Timeout(connect=30.0, read=300.0, write=None, pool=None), follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1 << 16):
                    f.write(chunk)
        tmp.rename(model_path)
        logger.info("FairFace model saved to %s in %.1fs", model_path, time.monotonic() - t0)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
