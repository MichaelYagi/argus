"""Detection routes — POST /api/detect/faces|objects|all."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import settings_cache
from app.core.auth import require_auth
from app.core.engine_registry import registry
from app.core.image_input import acquire_image, fetch_url, open_and_validate, to_rgb_array
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
    label = await _extract_label(request)
    replace = await _extract_replace(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    if replace:
        _clear_detections(user_id, source_id, "face")
    return {"source_image_id": source_id, "faces": _run_faces(user_id, img, source_id, label=label)}


@router.post("/api/detect/objects")
async def detect_objects(request: Request, user_id: int = Depends(require_auth)):
    raw = await acquire_image(request)
    replace = await _extract_replace(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    if replace:
        _clear_detections(user_id, source_id, "object")
    return {"source_image_id": source_id, "objects": _run_objects(user_id, img, source_id)}


@router.post("/api/detect/all")
async def detect_all(request: Request, user_id: int = Depends(require_auth)):
    raw = await acquire_image(request)
    label = await _extract_label(request)
    replace = await _extract_replace(request)
    img = open_and_validate(raw)
    source_filename, source_id = _save_source_image(user_id, raw, img)
    if replace:
        _clear_detections(user_id, source_id, None)  # both faces and objects
    return {
        "source_image_id": source_id,
        "faces": _run_faces(user_id, img, source_id, label=label),
        "objects": _run_objects(user_id, img, source_id),
    }


@router.post("/api/detect/bulk")
async def detect_bulk(request: Request, user_id: int = Depends(require_auth)):
    """Batch detect across multiple images.

    Multipart: one or more ``file`` fields plus optional ``type`` field.
    JSON: ``{"image_urls": [...], "type": "faces|objects|all"}``
    """
    content_type = request.headers.get("content-type", "")
    detect_type = "all"
    jobs: list[tuple[str, bytes]] = []  # (label, raw_bytes)

    if "multipart/form-data" in content_type:
        form = await request.form()
        detect_type = str(form.get("type", "all"))
        files = form.getlist("file")
        if not files:
            raise HTTPException(400, "No files provided")
        for f in files:
            jobs.append((getattr(f, "filename", "") or "upload", await f.read()))
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        detect_type = body.get("type", "all")
        urls = body.get("image_urls", [])
        if not urls:
            raise HTTPException(400, "No image_urls provided")
        for url in urls:
            try:
                jobs.append((url, await fetch_url(url)))
            except HTTPException as exc:
                jobs.append((url, b"__error__:" + exc.detail.encode()))
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    if detect_type not in ("faces", "objects", "all"):
        raise HTTPException(400, "type must be faces, objects, or all")

    results = []
    for i, (label, raw) in enumerate(jobs):
        base: dict = {"index": i, "filename": label}
        if raw.startswith(b"__error__:"):
            base["error"] = raw[len(b"__error__:"):].decode()
            results.append(base)
            continue
        try:
            img = open_and_validate(raw)
            _, src_id = _save_source_image(user_id, raw, img)
            base["source_image_id"] = src_id
            if detect_type in ("faces", "all"):
                base["faces"] = _run_faces(user_id, img, src_id)
            if detect_type in ("objects", "all"):
                base["objects"] = _run_objects(user_id, img, src_id)
        except HTTPException as exc:
            base["error"] = exc.detail
        except Exception as exc:
            base["error"] = str(exc)
        results.append(base)

    return {"total": len(results), "type": detect_type, "results": results}


# ---------------------------------------------------------------------------
# Detection pipelines
# ---------------------------------------------------------------------------

async def _extract_label(request: Request) -> str | None:
    """Read the optional `label` field from multipart form or JSON body."""
    ct = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in ct:
            form = await request.form()
            v = form.get("label")
            return str(v).strip() or None if v else None
        if "application/json" in ct:
            body = await request.json()
            v = body.get("label", "")
            return str(v).strip() or None
    except Exception:
        pass
    return None


def _is_truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


async def _extract_replace(request: Request) -> bool:
    """Read the optional `replace` flag (multipart form, JSON body, or query param).

    When true, the image's existing detections of the type being run are deleted
    before new ones are written — making re-detection of the same image idempotent.
    """
    if _is_truthy(request.query_params.get("replace", "")):
        return True
    ct = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in ct:
            form = await request.form()
            return _is_truthy(form.get("replace", ""))
        if "application/json" in ct:
            body = await request.json()
            return _is_truthy(body.get("replace", ""))
    except Exception:
        pass
    return False


def _clear_detections(user_id: int, source_id: int, det_type: str | None) -> None:
    """Remove prior detections (and their crop files) for a source image."""
    crops = store.clear_detections_for_source(source_id, user_id, det_type)
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
        except OSError:
            pass


def _run_faces(user_id: int, img: Any, source_id: int, label: str | None = None) -> list[dict]:
    model_row = store.get_active_model("face")
    if model_row is None:
        raise HTTPException(503, "No active face model. Download and activate one via /api/models.")

    engine = registry.get_face_engine()
    if engine is None:
        raise HTTPException(503, "Face engine not loaded. Activate a model via /api/models/{id}/activate.")

    threshold = settings_cache.cache.get_or("face.match_threshold", 0.5)
    auto_confirm_on = settings_cache.cache.get_or("face.auto_confirm", True)
    auto_confirm = settings_cache.cache.get_or("face.auto_confirm_threshold", 0.80)
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    save_unknown = settings_cache.cache.get_or("system.save_unknown_detections", True)

    img_array = to_rgb_array(img)
    detections = engine.detect(img_array)

    results = []
    for det in detections:
        identity_id, sim = _match_face(det.embedding, model_row["id"], user_id, threshold)

        if label:
            # Caller already knows who this is — assign directly and confirm.
            # The identity is asserted, not matched, so report full confidence
            # rather than the incidental score against the prior reference set.
            identity_id = store.get_or_create_identity(user_id, "face", label)
            sim = 1.0
            review_status = "confirmed"
        else:
            if identity_id is None and not save_unknown:
                continue
            review_status = (
                "confirmed"
                if identity_id is not None and auto_confirm_on and sim >= auto_confirm
                else "pending"
            )

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
            embedding=_embedding_to_bytes(det.embedding),
            review_status=review_status,
        )

        display_label = label
        if display_label is None and identity_id is not None:
            row = store.get_identity(identity_id, user_id)
            display_label = row["label"] if row else None

        if label:
            from app.api.enroll import enroll_from_detection
            enroll_from_detection(store.get_detection(detection_id, user_id), user_id)

        results.append({
            "detection_id": detection_id,
            "bbox": {"x": det.bbox[0], "y": det.bbox[1], "w": det.bbox[2], "h": det.bbox[3]},
            "confidence": det.confidence,        # face-detection quality score
            "similarity": round(float(sim), 4),  # match strength vs the enrolled identity
            "identity_id": identity_id,
            "label": display_label,
            "crop_url": f"/media/crops/{crop_filename}",
            "review_status": review_status,
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


def _embedding_to_bytes(embedding: Any) -> bytes | None:
    try:
        import numpy as np
        result = np.asarray(embedding, dtype=np.float32).tobytes()
        return result if isinstance(result, bytes) else None
    except Exception:
        return None


def _match_face(
    embedding: Any, model_id: int, user_id: int, threshold: float
) -> tuple[int | None, float]:
    from app.core import face_index
    results = face_index.search(embedding, user_id, threshold=threshold, k=1)
    if results:
        return results[0]
    # Below threshold — get best anyway for review queue context
    best = face_index.search(embedding, user_id, threshold=0.0, k=1)
    return (None, best[0][1]) if best else (None, 0.0)
