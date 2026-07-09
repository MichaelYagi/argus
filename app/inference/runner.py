"""Inference phase — pure engine calls, no DB writes, no file I/O.

This module is the seam between the inference service (model loading and
detection) and the persistence layer (DB, crop saves, matching).

**In-process mode (default)**: calls the registry and engines directly.
**Remote mode**: set INFERENCE_URL=http://host:8200 and every infer_faces /
infer_objects call becomes an HTTP POST to the inference sidecar instead.
The two modes are transparent to every caller — the same numpy array goes in,
the same FaceDetection / ObjectDetection list comes out.
"""
from __future__ import annotations

import base64
import os
from typing import Any

from fastapi import HTTPException

from app.db import store
from app.inference.registry import registry

# ---------------------------------------------------------------------------
# URL check
# ---------------------------------------------------------------------------

def _inference_url() -> str | None:
    """Return the inference sidecar base URL, or None for in-process mode."""
    return os.environ.get("INFERENCE_URL", "").strip() or None


# ---------------------------------------------------------------------------
# Array serialization (remote path only)
# ---------------------------------------------------------------------------

def _encode_array(img_array: Any) -> tuple[str, list]:
    """Serialize a numpy uint8 RGB array to (base64_str, shape_list)."""
    return base64.b64encode(img_array.tobytes()).decode(), list(img_array.shape)


# ---------------------------------------------------------------------------
# HTTP helpers (remote path)
# ---------------------------------------------------------------------------

def _remote_post(url: str, path: str, payload: dict) -> dict:
    """POST to the inference sidecar; propagate 503 as HTTPException."""
    import httpx

    try:
        resp = httpx.post(f"{url}{path}", json=payload, timeout=60.0)
    except httpx.RequestError as exc:
        raise HTTPException(503, f"Inference service unreachable: {exc}") from exc
    if resp.status_code == 503:
        raise HTTPException(503, resp.json().get("detail", "Inference service unavailable"))
    resp.raise_for_status()
    return resp.json()


def _remote_infer_faces(img_array: Any, url: str) -> tuple[list[Any], Any]:
    import numpy as np

    from app.inference.face_engine import FaceDetection

    array_b64, array_shape = _encode_array(img_array)
    data = _remote_post(url, "/infer/faces", {"array_b64": array_b64, "array_shape": array_shape})

    model_row = {"id": data["model_id"], "name": data["model_name"]}
    faces = []
    for f in data["faces"]:
        emb = None
        if f.get("embedding") is not None:
            raw_emb = base64.b64decode(f["embedding"])
            emb = np.frombuffer(raw_emb, dtype="float32")
            if f.get("embedding_shape"):
                emb = emb.reshape(f["embedding_shape"])
        faces.append(FaceDetection(
            bbox=tuple(f["bbox"]),
            confidence=f["confidence"],
            embedding=emb,
            age=f.get("age"),
            gender=f.get("gender"),
            pose=tuple(f["pose"]) if f.get("pose") else None,
            mask=f.get("mask"),
            kps=f.get("kps"),
            landmark_2d_106=f.get("landmark_2d_106"),
            landmark_3d_68=f.get("landmark_3d_68"),
        ))
    return faces, model_row


def _remote_infer_objects(img_array: Any, url: str) -> tuple[list[Any], list[str] | None, Any]:
    from app.inference.object_engine import ObjectDetection

    array_b64, array_shape = _encode_array(img_array)
    data = _remote_post(url, "/infer/objects", {"array_b64": array_b64, "array_shape": array_shape})

    model_row = {"id": data["model_id"], "name": data["model_name"]}
    image_tags = data.get("image_tags")
    objects = [
        ObjectDetection(
            bbox=tuple(o["bbox"]),
            confidence=o["confidence"],
            class_name=o["class_name"],
            class_id=o["class_id"],
        )
        for o in data["objects"]
    ]
    return objects, image_tags, model_row


# ---------------------------------------------------------------------------
# Public interface — called by all detection / enrollment routes
# ---------------------------------------------------------------------------

def infer_faces(img_array: Any) -> tuple[list[Any], Any]:
    """Detect faces. Routes to the inference sidecar when INFERENCE_URL is set."""
    url = _inference_url()
    if url:
        return _remote_infer_faces(img_array, url)

    model_row = store.get_active_model("face")
    if model_row is None:
        raise HTTPException(503, "No active face model. Download and activate one via /api/models.")
    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")
    return engine.detect(img_array), model_row


def infer_objects(img_array: Any) -> tuple[list[Any], list[str] | None, Any]:
    """Detect objects. Routes to the inference sidecar when INFERENCE_URL is set."""
    url = _inference_url()
    if url:
        return _remote_infer_objects(img_array, url)

    model_row = store.get_active_model("object")
    if model_row is None:
        raise HTTPException(503, "No active object model. Download and activate one via /api/models.")
    engine = registry.get_object_engine()
    if engine is None:
        raise HTTPException(503, "Object engine not loaded. Activate a model via /api/models/{id}/activate.")
    if getattr(engine, "has_image_tags", False):
        image_tags, raw_dets = engine.detect_with_tags(img_array)
    else:
        image_tags, raw_dets = None, engine.detect(img_array)
    return raw_dets, image_tags, model_row
