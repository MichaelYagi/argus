"""InsightFace wrapper — RetinaFace detection + ArcFace embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import accelerator, settings_cache


@dataclass
class FaceDetection:
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    embedding: Any  # numpy float32 array, shape (embedding_dim,)
    # Optional facial attributes from the model pack (genderage + 3d68 pose).
    # None when a model/face object doesn't provide them — never required.
    age: int | None = None
    gender: str | None = None            # 'M' or 'F'
    pose: tuple[float, float, float] | None = None  # (pitch, yaw, roll), degrees


def _face_providers() -> list[str]:
    """ONNX providers for InsightFace (capability-selected; CUDA or CPU)."""
    return accelerator.face_runtime()[0]


def _face_ctx_id() -> int:
    """Return InsightFace ctx_id: 0 = GPU, -1 = CPU."""
    return accelerator.face_runtime()[1]


class FaceEngine:
    def __init__(self, model_name: str, model_root: Path) -> None:
        from insightface.app import FaceAnalysis

        self._model_name = model_name
        # Pass providers explicitly so onnxruntime is never handed a provider it can't
        # use (which it would warn about and ignore), and keep ctx_id consistent.
        providers, ctx_id = accelerator.face_runtime()
        self._app = FaceAnalysis(name=model_name, root=str(model_root), providers=providers)
        self._app.prepare(ctx_id=ctx_id, det_size=(640, 640))

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
                age=_attr_age(face),
                gender=_attr_gender(face),
                pose=_attr_pose(face),
            ))
        return detections


# ---------------------------------------------------------------------------
# Attribute extraction — defensive: any missing/odd value yields None, never raises
# ---------------------------------------------------------------------------

def _attr_age(face: Any) -> int | None:
    try:
        age = getattr(face, "age", None)
        return int(age) if age is not None else None
    except (TypeError, ValueError):
        return None


def _attr_gender(face: Any) -> str | None:
    # InsightFace exposes `.sex` ('M'/'F') derived from the genderage model.
    try:
        sex = getattr(face, "sex", None)
        if sex in ("M", "F"):
            return sex
        gender = getattr(face, "gender", None)  # fallback: 1=male, 0=female
        if gender in (0, 1):
            return "M" if gender == 1 else "F"
    except (TypeError, ValueError):
        pass
    return None


def _attr_pose(face: Any) -> tuple[float, float, float] | None:
    try:
        pose = getattr(face, "pose", None)
        if pose is None:
            return None
        vals = [round(float(v), 1) for v in pose]
        if len(vals) != 3:
            return None
        return (vals[0], vals[1], vals[2])
    except (TypeError, ValueError):
        return None
