"""InsightFace wrapper — RetinaFace detection + ArcFace embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import settings_cache


@dataclass
class FaceDetection:
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    embedding: Any  # numpy float32 array, shape (embedding_dim,)


def _face_ctx_id() -> int:
    """Return InsightFace ctx_id: 0 = GPU, -1 = CPU."""
    if not settings_cache.cache.get_or("system.use_gpu", True):
        return -1
    try:
        import onnxruntime as ort
        return 0 if "CUDAExecutionProvider" in ort.get_available_providers() else -1
    except ImportError:
        return -1


class FaceEngine:
    def __init__(self, model_name: str, model_root: Path) -> None:
        from insightface.app import FaceAnalysis

        self._model_name = model_name
        self._app = FaceAnalysis(name=model_name, root=str(model_root))
        self._app.prepare(ctx_id=_face_ctx_id(), det_size=(640, 640))

    @property
    def model_name(self) -> str:
        return self._model_name

    def detect(self, image: Any) -> list[FaceDetection]:
        import numpy as np

        min_conf = settings_cache.cache.get_or("face.detection_confidence", 0.6)
        min_size = settings_cache.cache.get_or("face.min_face_size", 40)

        detections: list[FaceDetection] = []
        for face in self._app.get(image):
            if float(face.det_score) < min_conf:
                continue
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            w, h = x2 - x1, y2 - y1
            if w < min_size or h < min_size:
                continue
            detections.append(FaceDetection(
                bbox=(x1, y1, w, h),
                confidence=float(face.det_score),
                embedding=face.embedding.astype(np.float32),
            ))
        return detections
