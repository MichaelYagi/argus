"""Standalone FastAPI app exposing inference over HTTP.

Endpoints:
  GET  /infer/health          — liveness + which models are loaded
  POST /infer/faces           — detect faces in an image
  POST /infer/objects         — detect objects in an image

Request body (both POST endpoints):
  { "array_b64": "<base64 of img_array.tobytes()>", "array_shape": [H, W, 3] }

Embeddings in face responses are base64-encoded little-endian float32 arrays
with a companion "embedding_shape" field so the caller can reconstruct them:
  import numpy as np, base64
  arr = np.frombuffer(base64.b64decode(face["embedding"]), dtype="float32")
  arr = arr.reshape(face["embedding_shape"])

Auth: none — this is a LAN-internal sidecar, same trust assumption as the
page routes on the main app.

When run as a sidecar (python -m app.inference), this process reads the active
model configuration from the same SQLite DB as the main app and loads the
engines at startup. Hot-swap is not yet supported in standalone mode — that
comes in a later phase when the full sidecar split is in place.
"""

from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core import settings_cache
from app.db import store
from app.inference.registry import registry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    settings_cache.cache.load()
    _autoload_engines()
    yield


def _autoload_engines() -> None:
    from app.core.paths import models_dir

    face_row = store.get_active_model("face")
    if face_row and face_row["is_downloaded"]:
        try:
            from app.inference.face_engine import FaceEngine
            registry.swap_face_engine(FaceEngine(face_row["name"], models_dir()))
            log.info("Loaded face model: %s", face_row["name"])
        except Exception as exc:
            log.warning("Failed to load face model %s: %s", face_row["name"], exc, exc_info=True)

    obj_row = store.get_active_model("object")
    if obj_row and obj_row["is_downloaded"]:
        try:
            name = obj_row["name"]
            if name.lower() == "ram-plus-plus-grounding-dino":
                from app.inference.tagger_engine import TaggerEngine
                registry.swap_object_engine(TaggerEngine(models_dir()))
            elif name.lower().startswith("florence"):
                from app.inference.florence_engine import FlorenceEngine
                registry.swap_object_engine(FlorenceEngine(models_dir()))
            else:
                from app.inference.object_engine import ObjectEngine
                registry.swap_object_engine(ObjectEngine(name, models_dir() / f"{name}.pt"))
            log.info("Loaded object model: %s", obj_row["name"])
        except Exception as exc:
            log.warning("Failed to load object model %s: %s", obj_row["name"], exc, exc_info=True)


app = FastAPI(title="Argus Inference", lifespan=lifespan, docs_url=None)


# ---------------------------------------------------------------------------
# Request / serialization helpers
# ---------------------------------------------------------------------------

class InferRequest(BaseModel):
    array_b64: str        # base64 of img_array.tobytes() (uint8 RGB)
    array_shape: list[int]  # [H, W, 3]


def _b64_to_array(array_b64: str, array_shape: list[int]) -> Any:
    """Reconstruct a numpy uint8 RGB array from its serialized form."""
    import numpy as np

    data = base64.b64decode(array_b64)
    return np.frombuffer(data, dtype="uint8").reshape(array_shape)


def _face_to_dict(face: Any) -> dict:
    emb = face.embedding
    if emb is not None:
        emb_b64 = base64.b64encode(emb.tobytes()).decode()
        emb_shape = list(emb.shape)
    else:
        emb_b64 = None
        emb_shape = None
    return {
        "bbox":             list(face.bbox),
        "confidence":       face.confidence,
        "embedding":        emb_b64,
        "embedding_shape":  emb_shape,
        "age":              face.age,
        "gender":           face.gender,
        "pose":             list(face.pose) if face.pose is not None else None,
        "mask":             face.mask,
        "kps":              face.kps,
        "landmark_2d_106":  face.landmark_2d_106,
        "landmark_3d_68":   face.landmark_3d_68,
    }


def _object_to_dict(obj: Any) -> dict:
    return {
        "bbox":        list(obj.bbox),
        "confidence":  obj.confidence,
        "class_name":  obj.class_name,
        "class_id":    obj.class_id,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/infer/health")
async def health():
    face_row   = store.get_active_model("face")
    object_row = store.get_active_model("object")
    return {
        "status":       "ok",
        "face_model":   face_row["name"]   if face_row   and registry.get_face_engine()   else None,
        "object_model": object_row["name"] if object_row and registry.get_object_engine() else None,
    }


@app.post("/infer/faces")
async def infer_faces(body: InferRequest):
    model_row = store.get_active_model("face")
    if model_row is None:
        raise HTTPException(503, "No active face model.")
    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded.")
    img_array = _b64_to_array(body.array_b64, body.array_shape)
    faces = engine.detect(img_array)
    return {
        "model_id":   model_row["id"],
        "model_name": model_row["name"],
        "faces":      [_face_to_dict(f) for f in faces],
    }


@app.post("/infer/objects")
async def infer_objects(body: InferRequest):
    model_row = store.get_active_model("object")
    if model_row is None:
        raise HTTPException(503, "No active object model.")
    engine = registry.get_object_engine()
    if engine is None:
        raise HTTPException(503, "Object engine not loaded.")
    img_array = _b64_to_array(body.array_b64, body.array_shape)
    if getattr(engine, "has_image_tags", False):
        image_tags, raw_dets = engine.detect_with_tags(img_array)
    else:
        image_tags, raw_dets = None, engine.detect(img_array)
    return {
        "model_id":   model_row["id"],
        "model_name": model_row["name"],
        "image_tags": image_tags,
        "objects":    [_object_to_dict(o) for o in raw_dets],
    }
