"""Face enrollment routes — POST /api/faces/enroll and POST /api/identities/{id}/enroll."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.core import settings_cache
from app.core.auth import require_auth, require_env_id
from app.core.image_input import acquire_image, open_and_validate, read_body_field, to_rgb_array
from app.core.paths import crops_dir
from app.db import store
from app.inference.runner import infer_faces

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _active_face_model_id() -> int | None:
    row = store.get_active_model("face")
    return row["id"] if row else None


def enroll_from_detection(det: Any, user_id: int, environment_id: int | None = None) -> bool:
    """Copy a detection's embedding into face_embeddings.

    Returns True if a new embedding was added, False if already present.
    """
    if not det or not det["embedding"] or not det["identity_id"]:
        return False

    # Use the model that produced this detection's embedding; fall back to the
    # currently active model for detections created before model_id was recorded.
    try:
        model_id = int(det["model_id"]) if det["model_id"] is not None else _active_face_model_id()
    except (KeyError, TypeError):
        model_id = _active_face_model_id()

    if store.embedding_exists(det["identity_id"], det["crop_path"]):
        return False

    env_id = _det_env(det, environment_id)
    store.insert_face_embedding(
        identity_id=det["identity_id"],
        model_id=model_id,
        embedding_bytes=bytes(det["embedding"]),
        source_image_path=det["crop_path"],
        environment_id=env_id,
        confidence=float(det["confidence"] or 0.5),
    )
    if model_id:
        store.compute_and_store_representative(det["identity_id"], model_id)
        from app.core import face_index as _fi
        _fi.update_identity(user_id, env_id, det["identity_id"])
    from app.core import webhook as _wh
    _wh.fire(user_id, env_id, "identity.updated", {
        "identity_id": det["identity_id"],
        "action": "embedding_added",
        "detection_id": det["id"],
    })
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
        _fi.update_identity(user_id, environment_id, det["identity_id"])
        from app.core import webhook as _wh
        _wh.fire(user_id, environment_id, "identity.updated", {
            "identity_id": det["identity_id"],
            "action": "embedding_removed",
            "detection_id": detection_id,
        })
    return {"detection_id": detection_id, "identity_id": det["identity_id"],
            "removed": removed, "enrolled": False}


@router.delete("/api/face_embeddings/{embedding_id}", status_code=204)
async def delete_embedding(
    embedding_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    emb = store.get_face_embedding(embedding_id, user_id, environment_id)
    if not emb or not store.delete_face_embedding(embedding_id, user_id, environment_id):
        raise HTTPException(404, "Embedding not found")
    if _active_face_model_id():
        from app.core import face_index as _fi
        _fi.update_identity(user_id, environment_id, emb["identity_id"])
    from app.core import webhook as _wh
    _wh.fire(user_id, environment_id, "identity.updated", {
        "identity_id": emb["identity_id"],
        "action": "embedding_removed",
        "detection_id": None,
    })


@router.get("/api/face_embeddings")
async def list_embeddings(
    identity_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """List reference embeddings for an identity (without the raw vectors)."""
    if not store.get_identity(identity_id, user_id, environment_id):
        raise HTTPException(404, "Identity not found")
    return [dict(r) for r in store.list_face_embeddings(identity_id)]


@router.get("/api/face_embeddings/{embedding_id}")
async def get_embedding(
    embedding_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    row = store.get_face_embedding(embedding_id, user_id, environment_id)
    if not row:
        raise HTTPException(404, "Embedding not found")
    return dict(row)


@router.get("/api/face_embeddings/{embedding_id}/img")
async def get_embedding_img(
    embedding_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    row = store.get_face_embedding(embedding_id, user_id, environment_id)
    if not row:
        raise HTTPException(404, "Embedding not found")
    if not row["source_image_path"]:
        raise HTTPException(404, "No image stored for this embedding")
    path = crops_dir() / row["source_image_path"]
    if not path.exists():
        raise HTTPException(404, "Image not found on disk")
    return FileResponse(path, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/faces/enroll", status_code=201)
async def enroll_new(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Create a new face identity and store its first embedding in one call."""
    raw, name, external_ref = await _parse_enroll_request(request)
    img = open_and_validate(raw)
    embedding, face_det = _extract_embedding(raw, img)

    try:
        identity_id = store.create_identity(user_id, "face", name, environment_id, external_ref)
    except store.DuplicateError:
        raise HTTPException(409, f"Identity '{name}' already exists")

    result = _persist_enrollment(
        identity_id, user_id, environment_id,
        raw, img, face_det, embedding, external_ref, background_tasks,
    )
    store.set_identity_cover(identity_id, user_id, result["detection_id"], environment_id)

    from app.core import activity_buffer as _ab
    from app.core import webhook as _wh
    _ab.emit("enrollment", f"New face enrolled: {name}")
    _wh.fire(user_id, environment_id, "identity.created",
             {"identity_id": identity_id, "label": name, "type": "face", "external_ref": external_ref})
    return {
        "identity_id": identity_id, "label": name, "embeddings": 1,
        "embedding_id": result["embedding_id"], "detection_id": result["detection_id"],
        "external_ref": external_ref,
        "source_image_id": result["source_id"],
        "source_image_url": f"/media/sources/{result['source_filename']}",
        "source_scale": result["source_scale"],
        "bbox": {"x": face_det.bbox[0], "y": face_det.bbox[1],
                 "w": face_det.bbox[2], "h": face_det.bbox[3]},
    }


@router.post("/api/identities/{identity_id}/enroll", status_code=201)
async def enroll_existing(
    identity_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Add a reference embedding to an existing face identity."""
    if not store.get_identity(identity_id, user_id, environment_id):
        raise HTTPException(404, "Identity not found")

    raw, _name, _ext = await _parse_enroll_request(request, name_required=False)
    img = open_and_validate(raw)
    embedding, face_det = _extract_embedding(raw, img)

    result = _persist_enrollment(
        identity_id, user_id, environment_id,
        raw, img, face_det, embedding, None, background_tasks,
    )
    identity = store.get_identity(identity_id, user_id, environment_id)
    from app.core import activity_buffer as _ab
    _ab.emit("enrollment", f"Face sample added to {identity['label'] if identity else identity_id}")
    return {
        "embedding_id": result["embedding_id"],
        "identity_id": identity_id,
        "label": identity["label"] if identity else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _persist_enrollment(
    identity_id: int,
    user_id: int,
    environment_id: int,
    raw: bytes,
    img: Any,
    face_det: Any,
    embedding: Any,
    external_ref: str | None,
    background_tasks: BackgroundTasks,
) -> dict:
    """Save source image + crop, insert detection + embedding, rebuild index.

    Returns {detection_id, embedding_id, source_id, crop_path}.
    """
    from app.api.detect import _save_crop, _save_source_image, _scale_bbox

    model_id = _active_face_model_id()

    source_filename, source_id, source_scale = _save_source_image(user_id, environment_id, raw, img, external_ref)
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    # Crop from full-res original image at original bbox coords — preserves quality.
    crop_path = _save_crop(img, face_det.bbox, padding)
    # Scale bbox to stored source image's coordinate space for DB and tag page overlay.
    stored_bbox = _scale_bbox(face_det.bbox, 1.0 / source_scale)

    detection_id = store.insert_detection(
        user_id=user_id,
        environment_id=environment_id,
        identity_id=identity_id,
        source_image_id=source_id,
        detection_type="face",
        model_id=model_id,
        confidence=face_det.confidence,
        bbox_x=stored_bbox[0], bbox_y=stored_bbox[1],
        bbox_w=stored_bbox[2], bbox_h=stored_bbox[3],
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
        confidence=float(face_det.confidence or 0.5),
    )
    if model_id:
        store.compute_and_store_representative(identity_id, model_id)
        from app.core import face_index as _fi
        _fi.update_identity(user_id, environment_id, identity_id)
    from app.api.detect import scan_unidentified
    background_tasks.add_task(scan_unidentified, user_id, environment_id)
    return {
        "detection_id": detection_id,
        "embedding_id": embedding_id,
        "source_id": source_id,
        "source_filename": source_filename,
        "source_scale": source_scale,
        "crop_path": crop_path,
    }


async def _parse_enroll_request(
    request: Request, name_required: bool = True
) -> tuple[bytes, str, str | None]:
    """Extract image bytes, optional name, and optional external_ref from the request.

    acquire_image validates Content-Type and the one-of-three image input rule.
    Starlette caches form/JSON after first parse, so the read_body_field calls below are free.
    """
    raw = await acquire_image(request)
    name_v = await read_body_field(request, "name") or await read_body_field(request, "label")
    name = (name_v or "").strip()
    ext_v = await read_body_field(request, "external_ref")
    external_ref = (ext_v or "").strip() or None

    if name_required and not name:
        raise HTTPException(400, "'name' is required")

    return raw, name, external_ref


def _extract_embedding(raw: bytes, img: Any) -> tuple[Any, Any]:
    """Run face detection, return (embedding, face_detection).

    Uses the highest-confidence face if multiple are detected.
    Raises 503 if no engine is loaded, 400 if no face is found.
    """
    # Full-resolution inference — InsightFace is built for large inputs and
    # resizing degrades confidence. Matches the behaviour in _run_faces.
    faces, _ = infer_faces(to_rgb_array(img))

    if not faces:
        raise HTTPException(400, "No face detected in this image.")

    best = max(faces, key=lambda f: f.confidence)
    return best.embedding, best


def _to_bytes(embedding: Any) -> bytes:
    import numpy as np
    arr = np.asarray(embedding, dtype=np.float32)
    return arr.tobytes()
