"""Inference phase — pure engine calls, no DB writes, no file I/O.

This module is the seam between the inference service (model loading and
detection) and the persistence layer (DB, crop saves, matching). When the
inference container is introduced, the bodies of these two functions become
HTTP calls and everything else in the codebase stays the same.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.db import store
from app.inference.registry import registry


def infer_faces(img_array: Any) -> tuple[list[Any], Any]:
    """Run the face engine on img_array. Returns (raw_detections, model_row).
    Raises 503 if no active model or engine is loaded."""
    model_row = store.get_active_model("face")
    if model_row is None:
        raise HTTPException(503, "No active face model. Download and activate one via /api/models.")
    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")
    return engine.detect(img_array), model_row


def infer_objects(img_array: Any) -> tuple[list[Any], list[str] | None, Any]:
    """Run the object engine on img_array. Returns (raw_detections, image_tags, model_row).
    image_tags is None for non-tagger engines. Raises 503 if no active model or engine."""
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
