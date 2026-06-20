"""Face enrollment routes — POST /api/faces/enroll and POST /api/identities/{id}/enroll."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import require_auth
from app.core.engine_registry import registry
from app.core.image_input import decode_base64, fetch_url, open_and_validate, to_rgb_array
from app.core.paths import sources_dir
from app.db import store

router = APIRouter()


@router.delete("/api/face_embeddings/{embedding_id}", status_code=204)
async def delete_embedding(embedding_id: int, user_id: int = Depends(require_auth)):
    if not store.delete_face_embedding(embedding_id, user_id):
        raise HTTPException(404, "Embedding not found")


@router.get("/api/face_embeddings")
async def list_embeddings(
    identity_id: int,
    user_id: int = Depends(require_auth),
):
    """List reference embeddings for an identity (without the raw vectors)."""
    from app.db import store as _s
    if not _s.get_identity(identity_id, user_id):
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
async def enroll_new(request: Request, user_id: int = Depends(require_auth)):
    """Create a new face identity and store its first embedding in one call."""
    raw, name = await _parse_enroll_request(request)
    img = open_and_validate(raw)
    embedding, source_path = _extract_embedding(raw, img)

    try:
        identity_id = store.create_identity(user_id, "face", name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Identity '{name}' already exists")

    model_row = store.get_active_model("face")
    store.insert_face_embedding(
        identity_id=identity_id,
        model_id=model_row["id"] if model_row else None,
        embedding_bytes=_to_bytes(embedding),
        source_image_path=source_path,
    )
    return {"identity_id": identity_id, "label": name, "embeddings": 1}


@router.post("/api/identities/{identity_id}/enroll", status_code=201)
async def enroll_existing(
    identity_id: int, request: Request, user_id: int = Depends(require_auth)
):
    """Add a reference embedding to an existing face identity."""
    if not store.get_identity(identity_id, user_id):
        raise HTTPException(404, "Identity not found")

    raw, _name = await _parse_enroll_request(request, name_required=False)
    img = open_and_validate(raw)
    embedding, source_path = _extract_embedding(raw, img)

    model_row = store.get_active_model("face")
    embedding_id = store.insert_face_embedding(
        identity_id=identity_id,
        model_id=model_row["id"] if model_row else None,
        embedding_bytes=_to_bytes(embedding),
        source_image_path=source_path,
    )
    return {"embedding_id": embedding_id, "identity_id": identity_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _parse_enroll_request(
    request: Request, name_required: bool = True
) -> tuple[bytes, str]:
    """Extract image bytes and optional name from multipart form or JSON body."""
    content_type = request.headers.get("content-type", "")
    name = ""
    file_bytes: bytes | None = None
    image_url: str | None = None
    image_base64: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        name = str(form.get("name") or form.get("label") or "").strip()
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

    return raw, name


def _extract_embedding(raw: bytes, img: Any) -> tuple[Any, str | None]:
    """Run face detection, return (embedding, source_image_path).

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

    # Save source image to disk for reference
    source_path = _save_source(raw, img)
    return best.embedding, source_path


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
