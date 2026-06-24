"""Face enrollment routes — POST /api/faces/enroll and POST /api/identities/{id}/enroll."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import settings_cache
from app.core.auth import require_auth, require_env_id
from app.core.engine_registry import registry
from app.core.image_input import decode_base64, fetch_url, open_and_validate, to_rgb_array
from app.core.paths import crops_dir, sources_dir
from app.db import store

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helper — enroll from an existing detection's stored embedding
# ---------------------------------------------------------------------------

def enroll_from_detection(det: Any, user_id: int, environment_id: int | None = None) -> bool:
    """Copy a detection's embedding into face_embeddings.

    Returns True if a new embedding was added, False if already present.
    """
    if not det or not det["embedding"] or not det["identity_id"]:
        return False

    model_row = store.get_active_model("face")
    model_id  = model_row["id"] if model_row else None

    # Dedup: skip if this crop is already enrolled for this identity
    with store._connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM face_embeddings WHERE identity_id = ? AND source_image_path = ?",
            (det["identity_id"], det["crop_path"]),
        ).fetchone()
    if exists:
        return False

    store.insert_face_embedding(
        identity_id=det["identity_id"],
        model_id=model_id,
        embedding_bytes=bytes(det["embedding"]),
        source_image_path=det["crop_path"],
        environment_id=environment_id,
    )
    if model_id:
        store.compute_and_store_representative(det["identity_id"], model_id)
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id, _det_env(det, environment_id))
    return True


def _det_env(det: Any, environment_id: int | None) -> int:
    """Resolve the environment a detection belongs to for index rebuilds."""
    if environment_id is not None:
        return environment_id
    try:
        return int(det["environment_id"])
    except (IndexError, KeyError, TypeError):
        return 0


@router.post("/api/detections/{detection_id}/enroll", status_code=201)
async def enroll_detection(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Promote a confirmed detection's embedding to the identity's reference set."""
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    if det["type"] != "face":
        raise HTTPException(409, "Only face detections can be enrolled")
    if not det["identity_id"]:
        raise HTTPException(409, "Assign this detection to an identity before enrolling")
    if not det["embedding"]:
        raise HTTPException(409, "No embedding stored for this detection")

    added = enroll_from_detection(det, user_id, environment_id)
    return {"detection_id": detection_id, "identity_id": det["identity_id"], "added": added, "enrolled": True}


@router.delete("/api/detections/{detection_id}/enroll", status_code=200)
async def unenroll_detection(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Remove this detection's crop from the identity's reference set (inverse of enroll)."""
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    removed = store.remove_reference_by_detection(detection_id, user_id, environment_id)
    if removed:
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id, environment_id)
    return {"detection_id": detection_id, "identity_id": det["identity_id"],
            "removed": removed, "enrolled": False}


@router.delete("/api/face_embeddings/{embedding_id}", status_code=204)
async def delete_embedding(
    embedding_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.delete_face_embedding(embedding_id, user_id):
        raise HTTPException(404, "Embedding not found")
    model_row = store.get_active_model("face")
    if model_row:
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id, environment_id)


@router.get("/api/face_embeddings")
async def list_embeddings(
    identity_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """List reference embeddings for an identity (without the raw vectors)."""
    from app.db import store as _s
    if not _s.get_identity(identity_id, user_id, environment_id):
        raise HTTPException(404, "Identity not found")
    with store._connect() as conn:
        rows = conn.execute(
            """SELECT id, identity_id, model_id, source_image_path, created_at
               FROM face_embeddings WHERE identity_id = ? ORDER BY created_at""",
            (identity_id,),
        ).fetchall()
    return [dict(r) for r in rows]

_FMT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp",
             "GIF": "gif", "TIFF": "tif", "HEIF": "heif"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/faces/enroll", status_code=201)
async def enroll_new(
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Create a new face identity and store its first embedding in one call."""
    raw, name, external_ref = await _parse_enroll_request(request)
    img = open_and_validate(raw)
    embedding, source_filename, face_det = _extract_embedding(raw, img)

    try:
        identity_id = store.create_identity(user_id, "face", name, environment_id, external_ref)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Identity '{name}' already exists")

    model_row = store.get_active_model("face")
    model_id  = model_row["id"] if model_row else None

    source_id  = store.get_or_create_source_image(
        user_id, source_filename, img.width, img.height, environment_id, external_ref)
    crop_path  = _save_crop(img, face_det.bbox)
    detection_id = store.insert_detection(
        user_id=user_id,
        environment_id=environment_id,
        identity_id=identity_id,
        source_image_id=source_id,
        detection_type="face",
        model_id=model_id,
        confidence=face_det.confidence,
        bbox_x=face_det.bbox[0], bbox_y=face_det.bbox[1],
        bbox_w=face_det.bbox[2], bbox_h=face_det.bbox[3],
        crop_path=crop_path,
        embedding=_to_bytes(embedding),
        review_status="confirmed",
    )
    with store._connect() as conn:
        conn.execute("UPDATE identities SET cover_detection_id = ? WHERE id = ?",
                     (detection_id, identity_id))

    embedding_id = store.insert_face_embedding(
        identity_id=identity_id,
        model_id=model_id,
        embedding_bytes=_to_bytes(embedding),
        source_image_path=crop_path,
        environment_id=environment_id,
    )
    if model_id:
        store.compute_and_store_representative(identity_id, model_id)
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id, environment_id)
    return {"identity_id": identity_id, "label": name, "embeddings": 1,
            "embedding_id": embedding_id, "detection_id": detection_id,
            "external_ref": external_ref}


@router.post("/api/identities/{identity_id}/enroll", status_code=201)
async def enroll_existing(
    identity_id: int,
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Add a reference embedding to an existing face identity."""
    if not store.get_identity(identity_id, user_id, environment_id):
        raise HTTPException(404, "Identity not found")

    raw, _name, _ext = await _parse_enroll_request(request, name_required=False)
    img = open_and_validate(raw)
    embedding, source_filename, face_det = _extract_embedding(raw, img)

    model_row = store.get_active_model("face")
    model_id  = model_row["id"] if model_row else None

    source_id  = store.get_or_create_source_image(user_id, source_filename, img.width, img.height, environment_id)
    crop_path  = _save_crop(img, face_det.bbox)
    store.insert_detection(
        user_id=user_id,
        environment_id=environment_id,
        identity_id=identity_id,
        source_image_id=source_id,
        detection_type="face",
        model_id=model_id,
        confidence=face_det.confidence,
        bbox_x=face_det.bbox[0], bbox_y=face_det.bbox[1],
        bbox_w=face_det.bbox[2], bbox_h=face_det.bbox[3],
        crop_path=crop_path,
        embedding=_to_bytes(embedding),
        review_status="confirmed",
    )

    embedding_id = store.insert_face_embedding(
        identity_id=identity_id,
        model_id=model_id,
        embedding_bytes=_to_bytes(embedding),
        source_image_path=crop_path,
        environment_id=environment_id,
    )
    if model_id:
        store.compute_and_store_representative(identity_id, model_id)
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id, environment_id)
    identity = store.get_identity(identity_id, user_id, environment_id)
    return {
        "embedding_id": embedding_id,
        "identity_id": identity_id,
        "label": identity["label"] if identity else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _parse_enroll_request(
    request: Request, name_required: bool = True
) -> tuple[bytes, str, str | None]:
    """Extract image bytes, optional name, and optional external_ref from the request."""
    content_type = request.headers.get("content-type", "")
    name = ""
    external_ref: str | None = None
    file_bytes: bytes | None = None
    image_url: str | None = None
    image_base64: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        name = str(form.get("name") or form.get("label") or "").strip()
        external_ref = (str(form.get("external_ref")).strip() or None) if form.get("external_ref") else None
        file_field = form.get("file")
        if file_field is not None and hasattr(file_field, "read"):
            file_bytes = await file_field.read() or None
        raw_url = form.get("image_url")
        image_url = str(raw_url) if raw_url else None
        raw_b64 = form.get("image_base64")
        image_base64 = str(raw_b64) if raw_b64 else None
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        name = str(body.get("name") or body.get("label") or "").strip()
        external_ref = (str(body.get("external_ref")).strip() or None) if body.get("external_ref") else None
        image_url = body.get("image_url")
        image_base64 = body.get("image_base64")
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    if name_required and not name:
        raise HTTPException(400, "'name' is required")

    provided = sum(x is not None for x in [file_bytes, image_url, image_base64])
    if provided != 1:
        raise HTTPException(400, "Provide exactly one of: file, image_url, image_base64")

    if file_bytes is not None:
        raw = file_bytes
    elif image_url is not None:
        raw = await fetch_url(image_url)
    else:
        raw = decode_base64(image_base64)  # type: ignore[arg-type]

    return raw, name, external_ref


def _extract_embedding(raw: bytes, img: Any) -> tuple[Any, str | None, Any]:
    """Run face detection, return (embedding, source_filename, face_detection).

    Uses the highest-confidence face if multiple are detected.
    Raises 503 if no engine is loaded, 400 if no face is found.
    """
    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")

    img_array = to_rgb_array(img)
    faces = engine.detect(img_array)

    if not faces:
        raise HTTPException(400, "No face detected in this image.")

    best = max(faces, key=lambda f: f.confidence)
    source_path = _save_source(raw, img)
    return best.embedding, source_path, best


def _save_crop(img: Any, bbox: tuple) -> str:
    import uuid
    x, y, w, h = bbox
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    pad_x = int(w * padding)
    pad_y = int(h * padding)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(img.width,  x + w + pad_x)
    y2 = min(img.height, y + h + pad_y)
    crop = img.crop((x1, y1, x2, y2))
    crop_dir = crops_dir()
    crop_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    crop.convert("RGB").save(crop_dir / filename, "JPEG")
    return filename


def _save_source(raw: bytes, img: Any) -> str:
    content_hash = hashlib.sha256(raw).hexdigest()
    ext = _FMT_EXT.get(img.format or "JPEG", "jpg")
    filename = f"{content_hash}.{ext}"
    dest = sources_dir() / filename
    if not dest.exists():
        sources_dir().mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return filename


def _to_bytes(embedding: Any) -> bytes:
    import numpy as np
    arr = np.asarray(embedding, dtype=np.float32)
    return arr.tobytes()
