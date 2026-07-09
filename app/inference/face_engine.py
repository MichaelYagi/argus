"""InsightFace wrapper — RetinaFace detection + ArcFace embeddings."""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import settings_cache


@dataclass
class FaceDetection:
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    embedding: Any  # numpy float32 array, shape (embedding_dim,)
    # Optional facial attributes — None when the loaded model pack doesn't provide them.
    age: int | None = None
    gender: str | None = None                        # 'M' or 'F'
    pose: tuple[float, float, float] | None = None   # (pitch, yaw, roll), degrees
    mask: float | None = None                        # mask-wearing probability [0, 1]
    kps: list[list[float]] | None = None             # 5 keypoints [[x,y], ...]
    landmark_2d_106: list[list[float]] | None = None # 106 2D landmarks
    landmark_3d_68: list[list[float]] | None = None  # 68 3D landmarks


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
        with contextlib.redirect_stdout(io.StringIO()):
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
                age=_attr_age(face),
                gender=_attr_gender(face),
                pose=_attr_pose(face),
                mask=_attr_mask(face),
                kps=_attr_landmarks(face, "kps"),
                landmark_2d_106=_attr_landmarks(face, "landmark_2d_106"),
                landmark_3d_68=_attr_landmarks(face, "landmark_3d_68"),
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


def _attr_mask(face: Any) -> float | None:
    try:
        mask = getattr(face, "mask", None)
        if mask is None:
            return None
        return round(float(mask), 4)
    except (TypeError, ValueError):
        return None


def _attr_landmarks(face: Any, attr: str) -> list[list[float]] | None:
    try:
        pts = getattr(face, attr, None)
        if pts is None:
            return None
        return [[round(float(v), 2) for v in pt] for pt in pts]
    except (TypeError, ValueError):
        return None
