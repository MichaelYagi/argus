"""Detection routes — POST /api/detect/faces|objects|all."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import settings_cache
from app.core.auth import require_auth
from app.core.engine_registry import registry
from app.core.image_input import acquire_image, open_and_validate, to_rgb_array
from app.core.paths import crops_dir, sources_dir
from app.db import store

router = APIRouter()

_FMT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp",
             "GIF": "gif", "TIFF": "tif", "HEIF": "heif"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/detect/faces")
async def detect_faces(request: Request, user_id: int = Depends(require_auth)):
    raw = await acquire_image(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    return {"source_image_id": source_id, "faces": _run_faces(user_id, img, source_id)}


@router.post("/api/detect/objects")
async def detect_objects(request: Request, user_id: int = Depends(require_auth)):
    raw = await acquire_image(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    return {"source_image_id": source_id, "objects": _run_objects(user_id, img, source_id)}


@router.post("/api/detect/all")
async def detect_all(request: Request, user_id: int = Depends(require_auth)):
    raw = await acquire_image(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    return {
        "source_image_id": source_id,
        "faces": _run_faces(user_id, img, source_id),
        "objects": _run_objects(user_id, img, source_id),
    }


# ---------------------------------------------------------------------------
# Detection pipelines
# ---------------------------------------------------------------------------

def _run_faces(user_id: int, img: Any, source_id: int) -> list[dict]:
    model_row = store.get_active_model("face")
    if model_row is None:
        raise HTTPException(503, "No active face model. Download and activate one via /api/models.")

    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")

    threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    save_unknown = settings_cache.cache.get_or("system.save_unknown_detections", True)

    img_array = to_rgb_array(img)
    detections = engine.detect(img_array)

    results = []
    for det in detections:
        identity_id, _sim = _match_face(det.embedding, model_row["id"], user_id, threshold)

        if identity_id is None and not save_unknown:
            continue

        crop_filename = _save_crop(img, det.bbox, padding)
        detection_id = store.insert_detection(
            user_id=user_id,
            identity_id=identity_id,
            source_image_id=source_id,
            detection_type="face",
            model_id=model_row["id"],
            confidence=det.confidence,
            bbox_x=det.bbox[0],
            bbox_y=det.bbox[1],
            bbox_w=det.bbox[2],
            bbox_h=det.bbox[3],
            crop_path=crop_filename,
        )

        label = None
        if identity_id is not None:
            row = store.get_identity(identity_id, user_id)
            label = row["label"] if row else None

        results.append({
            "detection_id": detection_id,
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": det.confidence,
            "identity_id": identity_id,
            "label": label,
            "crop_url": f"/media/crops/{crop_filename}",
            "review_status": "pending",
        })

    return results


def _run_objects(user_id: int, img: Any, source_id: int) -> list[dict]:
    model_row = store.get_active_model("object")
    if model_row is None:
        raise HTTPException(503, "No active object model. Download and activate one via /api/models.")

    engine = registry.get_object_engine()
    if engine is None:
        raise HTTPException(503, "Object engine not loaded. Activate a model via /api/models/{id}/activate.")

    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)

    img_array = to_rgb_array(img)
    detections = engine.detect(img_array)

    results = []
    for det in detections:
        identity_id = store.get_or_create_identity(user_id, "object", det.class_name)
        crop_filename = _save_crop(img, det.bbox, padding)
        detection_id = store.insert_detection(
            user_id=user_id,
            identity_id=identity_id,
            source_image_id=source_id,
            detection_type="object",
            model_id=model_row["id"],
            confidence=det.confidence,
            bbox_x=det.bbox[0],
            bbox_y=det.bbox[1],
            bbox_w=det.bbox[2],
            bbox_h=det.bbox[3],
            crop_path=crop_filename,
        )
        results.append({
            "detection_id": detection_id,
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": det.confidence,
            "class_name": det.class_name,
            "class_id": det.class_id,
            "identity_id": identity_id,
            "label": det.class_name,
            "crop_url": f"/media/crops/{crop_filename}",
            "review_status": "pending",
        })

    return results


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_source_image(user_id: int, raw_bytes: bytes, img: Any) -> tuple[str, int]:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    ext = _FMT_EXT.get(img.format or "JPEG", "jpg")
    filename = f"{content_hash}.{ext}"
    dest = sources_dir() / filename
    if not dest.exists():
        sources_dir().mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
    source_id = store.get_or_create_source_image(user_id, filename, img.width, img.height)
    return filename, source_id


def _save_crop(img: Any, bbox: tuple[int, int, int, int], padding: float) -> str:
    x, y, w, h = bbox
    pad_x = int(w * padding)
    pad_y = int(h * padding)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(img.width, x + w + pad_x)
    y2 = min(img.height, y + h + pad_y)
    crop = img.crop((x1, y1, x2, y2))
    crop_dir = crops_dir()
    crop_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    crop.save(crop_dir / filename, "JPEG")
    return filename


def _match_face(
    embedding: Any, model_id: int, user_id: int, threshold: float
) -> tuple[int | None, float]:
    import numpy as np

    rows = store.get_face_embeddings_for_model(model_id, user_id)
    if not rows:
        return None, 0.0

    best_id: int | None = None
    best_sim = 0.0
    for row in rows:
        stored = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
        norm = np.linalg.norm(embedding) * np.linalg.norm(stored)
        sim = float(np.dot(embedding, stored) / norm) if norm > 0 else 0.0
        if sim > best_sim:
            best_sim = sim
            best_id = row["identity_id"]

    return (best_id, best_sim) if best_sim >= threshold else (None, best_sim)
