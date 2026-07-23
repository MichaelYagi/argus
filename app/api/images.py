"""Per-image face detection list, batch-tag, and reprocess endpoints."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api._responses import ERR_400, ERR_401, ERR_404, ERR_409, ok, ok201
from app.api._utils import delete_crops, delete_sources, is_truthy, paginate
from app.core import webhook as _webhook
from app.core.auth import require_auth, require_env_id
from app.core.paths import sources_dir
from app.db import store

logger = logging.getLogger(__name__)

router = APIRouter()


_VALID_SORTS = frozenset({"newest", "oldest", "most_detections", "fewest_detections", "last_modified"})


@router.get(
    "/api/images",
    responses={
        **ok({
            "items": [
                {
                    "source_image_id": 7,
                    "external_ref": "img_abc123",
                    "source_image_url": "/media/sources/abc456.jpg?h=300",
                    "width": 1920,
                    "height": 1080,
                    "face_count": 2,
                    "object_count": 1,
                    "detection_count": 3,
                    "uploaded_at": "2026-01-15T10:30:00Z",
                    "scene_tags": ["outdoor"],
                }
            ],
            "next_cursor": "2026-01-15T10:30:00Z_7",
            "has_more": False,
        }),
        **ERR_401,
        **ERR_400,
    },
)
async def list_source_images(
    cursor: str | None = Query(None),
    limit: int = Query(40, ge=1, le=200),
    identity_id: list[int] = Query(default=[]),
    type: str | None = Query(None, description="Filter by detection type: face or object"),
    since: str | None = Query(None, description="ISO timestamp — images uploaded at or after"),
    until: str | None = Query(None, description="ISO timestamp — images uploaded at or before"),
    external_ref: str | None = Query(None, description="Resolve images by caller-owned correlation id"),
    no_detections: bool = Query(False, description="Only return images with zero detections"),
    no_tagged_faces: bool = Query(False, description="Only return images with no identified faces"),
    no_crops: bool = Query(False, description="Only return images with no detection crops"),
    has_manual_detections: bool = Query(False, description="Images with at least one manually drawn detection"),
    sort: str = Query("newest", description="newest | oldest | most_detections | fewest_detections | last_modified"),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Paginated list of processed source images.
    Optional filters: identity_id (repeatable, AND semantics), type (face/object),
    since, until, external_ref, no_detections, no_tagged_faces, has_manual_detections.
    sort: newest (default), oldest, most_detections, fewest_detections, last_modified."""
    t0 = time.monotonic()
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    if sort not in _VALID_SORTS:
        raise HTTPException(400, f"sort must be one of: {', '.join(sorted(_VALID_SORTS))}")
    import asyncio
    if external_ref is not None:
        rows = store.list_source_images_by_ref(user_id, external_ref, environment_id)
        return {
            "items": [
                {
                    "source_image_id": r["id"],
                    "external_ref": r["external_ref"],
                    "width": r["width"],
                    "height": r["height"],
                    "source_image_url": f"/media/sources/{r['file_path']}",
                    "uploaded_at": r["uploaded_at"],
                    "scene_tags": json.loads(r["scene_tags"]) if r["scene_tags"] else [],
                }
                for r in rows
            ],
        }
    rows = await asyncio.to_thread(
        store.list_source_images,
        user_id, cursor=cursor, limit=limit, environment_id=environment_id,
        identity_ids=identity_id or None, detection_type=type, since=since, until=until,
        no_detections=no_detections, no_tagged_faces=no_tagged_faces, no_crops=no_crops,
        has_manual_detections=has_manual_detections, sort=sort,
    )
    if sort in ("most_detections", "fewest_detections"):
        cursor_fn = lambda r: f"{r['detection_count']}_{r['id']}"  # noqa: E731
    elif sort == "last_modified":
        cursor_fn = lambda r: f"{r['updated_at'] or r['uploaded_at']}_{r['id']}"  # noqa: E731
    else:
        cursor_fn = lambda r: f"{r['uploaded_at']}_{r['id']}"  # noqa: E731
    result = paginate(rows, limit, lambda r: {
        "source_image_id": r["id"],
        "external_ref": r["external_ref"],
        "source_image_url": f"/media/sources/{r['file_path']}?h=300",
        "width": r["width"],
        "height": r["height"],
        "face_count": r["face_count"],
        "object_count": r["object_count"],
        "detection_count": r["detection_count"],
        "uploaded_at": r["uploaded_at"],
        "updated_at": r["updated_at"],
        "scene_tags": json.loads(r["scene_tags"]) if r["scene_tags"] else [],
    }, cursor_fn=cursor_fn)
    logger.debug("GET /api/images: %d items sort=%s %.0fms",
                 len(result.get("items", [])), sort, (time.monotonic() - t0) * 1000)
    return result


@router.get(
    "/api/images/count",
    responses={**ok({"count": 95}), **ERR_401, **ERR_400},
)
async def count_source_images(
    identity_id: list[int] = Query(default=[]),
    type: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    no_detections: bool = Query(False),
    no_tagged_faces: bool = Query(False),
    no_crops: bool = Query(False),
    has_manual_detections: bool = Query(False),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Total count of source images matching the given filters."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    import asyncio
    count = await asyncio.to_thread(
        store.count_source_images_filtered,
        user_id, environment_id=environment_id,
        identity_ids=identity_id or None, detection_type=type,
        since=since, until=until,
        no_detections=no_detections, no_tagged_faces=no_tagged_faces, no_crops=no_crops,
        has_manual_detections=has_manual_detections,
    )
    return {"count": count}


@router.get(
    "/api/images/ids",
    responses={**ok({"ids": [7, 8, 9, 10, 11]}), **ERR_401, **ERR_400},
)
async def list_source_image_ids(
    identity_id: list[int] = Query(default=[]),
    type: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    no_detections: bool = Query(False),
    no_tagged_faces: bool = Query(False),
    no_crops: bool = Query(False),
    has_manual_detections: bool = Query(False),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """All source image IDs matching the given filters (no pagination, for select-all)."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    import asyncio
    ids = await asyncio.to_thread(
        store.list_source_image_ids,
        user_id, environment_id=environment_id,
        identity_ids=identity_id or None, detection_type=type,
        since=since, until=until,
        no_detections=no_detections, no_tagged_faces=no_tagged_faces, no_crops=no_crops,
        has_manual_detections=has_manual_detections,
    )
    return {"ids": ids}


def _parse_attributes(row) -> dict:
    """Parse the stored attributes JSON into {age, gender, pose}; all None if absent."""
    try:
        raw = row["attributes"]
    except (IndexError, KeyError):
        raw = None
    data = {}
    if raw:
        try:
            data = json.loads(raw) or {}
        except (ValueError, TypeError):
            data = {}
    return {"age": data.get("age"), "gender": data.get("gender"), "pose": data.get("pose")}


@router.get(
    "/api/images/{source_image_id}/faces",
    responses={
        **ok({
            "source_image_id": 7,
            "external_ref": "img_abc123",
            "width": 1920,
            "height": 1080,
            "uploaded_at": "2026-01-15T10:30:00Z",
            "source_image_url": "/media/sources/abc456.jpg",
            "scene_tags": [],
            "faces": [
                {
                    "detection_id": 101,
                    "bbox": {"x": 120, "y": 80, "w": 60, "h": 75},
                    "confidence": 0.9832,
                    "identity_id": 3,
                    "label": "Alice",
                    "crop_url": "/media/crops/abc123.jpg",
                    "review_status": "confirmed",
                    "embedding_source": "aligned",
                    "age": 32,
                    "gender": "F",
                    "pose": [1.2, -0.3, 0.1],
                }
            ],
        }),
        **ERR_401,
        **ERR_404,
    },
)
async def image_faces(
    source_image_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    src = store.get_source_image(source_image_id, user_id, environment_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    rows = store.get_image_detections(source_image_id, user_id, det_type="face", environment_id=environment_id)
    return {
        "source_image_id": source_image_id,
        "external_ref": src["external_ref"],
        "width": src["width"],
        "height": src["height"],
        "uploaded_at": src["uploaded_at"],
        "source_image_url": f"/media/sources/{src['file_path']}",
        "scene_tags": json.loads(src["scene_tags"]) if src["scene_tags"] else [],
        "faces": [
            {
                "detection_id": r["id"],
                "bbox": {"x": r["bbox_x"], "y": r["bbox_y"], "w": r["bbox_w"], "h": r["bbox_h"]},
                "confidence": r["confidence"],
                "identity_id": r["identity_id"],
                "label": r["identity_label"],
                "crop_url": f"/media/crops/{r['crop_path']}",
                "review_status": r["review_status"],
                "embedding_source": r["embedding_source"] if "embedding_source" in r.keys() else None,
                **_parse_attributes(r),
            }
            for r in rows
        ],
    }


@router.get(
    "/api/images/{source_image_id}/url",
    responses={
        **ok({"image_url": "/media/sources/abc456.jpg"}),
        **ERR_401,
        **ERR_404,
    },
)
async def get_source_image_url(
    source_image_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    src = store.get_source_image(source_image_id, user_id, environment_id)
    if not src:
        raise HTTPException(404, "Source image not found")
    return {"image_url": f"/media/sources/{src['file_path']}"}


@router.delete(
    "/api/images/{source_image_id}",
    status_code=200,
    responses={
        **ok({"source_image_id": 7, "detections_deleted": 3, "crops_removed": 3}),
        **ERR_401,
        **ERR_404,
    },
)
async def delete_source_image(
    source_image_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Delete a source image and cascade-delete all its detections (faces + objects).

    Use this before re-detecting a photo to avoid duplicate detections. References
    enrolled from the removed crops are dropped too, so no orphaned references remain.
    """
    result = store.delete_source_image(source_image_id, user_id, environment_id)
    if result is None:
        raise HTTPException(404, "Source image not found")

    deleted_ids, crops, src_file, _ = result
    removed = delete_crops(crops)
    if src_file:
        delete_sources([src_file])

    # References enrolled from the removed crops were dropped too — refresh the index.
    from app.core import face_index
    face_index.rebuild_user(user_id, environment_id)

    if deleted_ids:
        _webhook.fire(user_id, environment_id, "detection.deleted",
                      {"detection_ids": deleted_ids, "count": len(deleted_ids)})

    return {"source_image_id": source_image_id, "detections_deleted": len(deleted_ids),
            "crops_removed": removed}


@router.post(
    "/api/images/{source_image_id}/reprocess",
    status_code=200,
    responses={
        **ok({
            "source_image_id": 7,
            "faces": [
                {
                    "detection_id": 105,
                    "bbox": {"x": 120, "y": 80, "w": 60, "h": 75},
                    "confidence": 0.9832,
                    "identity_id": 3,
                    "label": "Alice",
                    "crop_url": "/media/crops/new001.jpg",
                    "review_status": "confirmed",
                }
            ],
            "objects": [],
        }),
        **ERR_401,
        **ERR_404,
        **ERR_400,
        **ERR_409,
    },
)
async def reprocess_source_image(
    source_image_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Re-run detection on an already-stored source image using the currently active models.

    Query params:
      type=faces|objects|all  (default: all)
      replace=true            (clear existing detections of that type first; default: false)
      async=true              (return a job_id and process in background; default: false)
    """
    src = store.get_source_image(source_image_id, user_id, environment_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    det_type = request.query_params.get("type", "all")
    if det_type not in ("faces", "objects", "all"):
        raise HTTPException(400, "type must be faces, objects, or all")
    replace = is_truthy(request.query_params.get("replace", "false"))
    run_async = is_truthy(request.query_params.get("async", "false"))

    source_path = sources_dir() / src["file_path"]
    if not source_path.exists():
        raise HTTPException(409, "Source file no longer on disk — cannot reprocess")
    raw = source_path.read_bytes()

    if run_async:
        from app.api.detect import _run_detection_job
        _DET_TYPE_SINGULAR = {"faces": "face", "objects": "object", "all": "all"}
        job_id = store.create_job(user_id, "reprocess", environment_id)
        background_tasks.add_task(
            _run_detection_job, job_id, user_id, environment_id,
            raw, None, replace, _DET_TYPE_SINGULAR[det_type], src["external_ref"],
        )
        return {"job_id": job_id, "status": "pending", "source_image_id": source_image_id}

    from app.api.detect import _cleanup_if_no_detections, _clear_detections, _run_faces, _run_objects
    from app.core.image_input import open_and_validate

    img = open_and_validate(raw)
    _DET_TYPE_SINGULAR = {"faces": "face", "objects": "object", "all": None}
    if replace:
        _clear_detections(user_id, environment_id, source_image_id, _DET_TYPE_SINGULAR[det_type])
    result: dict = {"source_image_id": source_image_id}
    if det_type in ("faces", "all"):
        result["faces"] = _run_faces(user_id, environment_id, img, source_image_id)
    if det_type in ("objects", "all"):
        objs, img_tags = _run_objects(user_id, environment_id, img, source_image_id)
        result["objects"] = objs
        if img_tags is not None:
            result["scene_tags"] = img_tags
    if _cleanup_if_no_detections(source_image_id, user_id, environment_id):
        result["discarded"] = True
    _webhook.fire(user_id, environment_id, "detection.created", {
        "source_image_id": source_image_id,
        "external_ref": src["external_ref"],
        "type": det_type,
    })
    return result


class _ManualBbox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class _ManualDetectionBody(BaseModel):
    bbox: _ManualBbox
    label: str | None = None
    identity_id: int | None = None


@router.post(
    "/api/images/{source_image_id}/detections",
    status_code=201,
    responses={
        **ok201({
            "detection_id": 110,
            "identity_id": 3,
            "label": "Alice",
            "bbox": {"x": 100, "y": 60, "w": 55, "h": 68},
            "crop_url": "/media/crops/man001.jpg",
            "source": "manual",
            "embedding_source": "aligned",
        }),
        **ERR_401,
        **ERR_404,
        **ERR_400,
        **ERR_409,
    },
)
async def create_manual_detection(
    source_image_id: int,
    body: _ManualDetectionBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Save a manually-drawn bounding box as a face detection.

    Attempts InsightFace recognition on the crop; if no face is found, the
    detection is saved without an embedding (label only). Requires exactly one
    of ``label`` or ``identity_id``.
    """
    if not body.label and not body.identity_id:
        raise HTTPException(400, "Provide label or identity_id")

    src = store.get_source_image(source_image_id, user_id, environment_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    bx, by, bw, bh = body.bbox.x, body.bbox.y, body.bbox.w, body.bbox.h
    if bw < 1 or bh < 1:
        raise HTTPException(400, "bbox w and h must be >= 1")
    if bx < 0 or by < 0 or bx + bw > src["width"] or by + bh > src["height"]:
        raise HTTPException(400, "bbox extends outside image bounds")

    source_path = sources_dir() / src["file_path"]
    if not source_path.exists():
        raise HTTPException(409, "Source file no longer on disk")

    from app.core.image_input import open_and_validate, to_rgb_array
    img = open_and_validate(source_path.read_bytes())

    from app.api.detect import _save_crop
    from app.core import settings_cache
    padding = settings_cache.cache.get_or("system.crop_padding", 0.2)
    crop_filename = _save_crop(img, (bx, by, bw, bh), padding)

    # Try InsightFace recognition on just the drawn bbox area.
    # Tier 1: full detect + embed (RetinaFace finds the face, ArcFace embeds it) → "aligned"
    # Tier 2: skip detection, call ArcFace directly on the raw crop → "raw"
    # Tier 3: save without embedding (falls through on any exception or empty result) → None
    embedding_bytes: bytes | None = None
    embedding_source: str | None = None
    try:
        from app.api.detect import _embedding_to_bytes
        from app.inference.runner import infer_face_embedding, infer_faces
        x1, y1 = max(0, bx), max(0, by)
        x2, y2 = min(img.width, bx + bw), min(img.height, by + bh)
        crop_img = img.crop((x1, y1, x2, y2))
        if crop_img.mode != "RGB":
            crop_img = crop_img.convert("RGB")
        crop_arr = to_rgb_array(crop_img)

        faces, _ = infer_faces(crop_arr)
        if faces:
            top_face = max(faces, key=lambda f: f.confidence)
            b = _embedding_to_bytes(top_face.embedding)
            if b is not None:
                embedding_bytes = b
                embedding_source = "aligned"

        if embedding_source is None:
            raw_feat = infer_face_embedding(crop_arr)
            if raw_feat is not None:
                b = _embedding_to_bytes(raw_feat)
                if b is not None:
                    embedding_bytes = b
                    embedding_source = "raw"
    except Exception:
        pass  # recognition is best-effort

    identity_id = body.identity_id
    identity_label: str | None = None
    if identity_id:
        identity = store.get_identity(identity_id, user_id, environment_id)
        if not identity:
            raise HTTPException(404, "Identity not found")
        identity_label = identity["label"]
    elif body.label:
        identity_id, _created = store.get_or_create_identity(
            user_id, "face", body.label.strip(), environment_id
        )
        if _created:
            _webhook.fire(user_id, environment_id, "identity.created", {
                "identity_id": identity_id, "label": body.label.strip(),
                "type": "face", "external_ref": None,
            })
        identity_label = body.label.strip()

    detection_id = store.insert_detection(
        user_id=user_id,
        environment_id=environment_id,
        identity_id=identity_id,
        source_image_id=source_image_id,
        detection_type="face",
        model_id=None,
        confidence=0.0,
        bbox_x=bx, bbox_y=by, bbox_w=bw, bbox_h=bh,
        crop_path=crop_filename,
        embedding=embedding_bytes,
        embedding_source=embedding_source,
        review_status="confirmed",
        source="manual",
    )

    if embedding_bytes and identity_id:
        det_row = store.get_detection(detection_id, user_id, environment_id)
        if det_row:
            from app.api.enroll import enroll_from_detection
            enroll_from_detection(det_row, user_id, environment_id)

    from app.core import face_index
    face_index.rebuild_user(user_id, environment_id)

    _webhook.fire(user_id, environment_id, "detection.created", {
        "source_image_id": source_image_id,
        "external_ref": src["external_ref"],
        "type": "face",
    })
    if identity_id:
        _webhook.fire(user_id, environment_id, "identity.updated", {
            "identity_id": identity_id,
            "action": "detection_added",
            "detection_id": detection_id,
        })

    return {
        "detection_id": detection_id,
        "identity_id": identity_id,
        "label": identity_label,
        "bbox": {"x": bx, "y": by, "w": bw, "h": bh},
        "crop_url": f"/media/crops/{crop_filename}",
        "source": "manual",
        "embedding_source": embedding_source,
    }


class _TagItem(BaseModel):
    detection_id: int
    identity_id: int | None = None
    label: str | None = None


@router.post(
    "/api/images/{source_image_id}/label",
    status_code=200,
    responses={
        **ok([
            {"detection_id": 101, "identity_id": 3, "label": "Alice", "status": "labeled"},
            {"detection_id": 102, "identity_id": 7, "label": "Bob", "status": "labeled"},
        ]),
        **ERR_401,
        **ERR_404,
    },
)
async def tag_image(
    source_image_id: int,
    items: list[_TagItem],
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    src = store.get_source_image(source_image_id, user_id, environment_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    results = []
    for item in items:
        det = store.get_detection(item.detection_id, user_id, environment_id)
        if not det or det["source_image_id"] != source_image_id:
            results.append({"detection_id": item.detection_id, "status": "not_found"})
            continue

        identity_id = item.identity_id
        if identity_id:
            if not store.get_identity(identity_id, user_id, environment_id):
                results.append({"detection_id": item.detection_id, "status": "error",
                                 "detail": "Identity not found"})
                continue
        elif item.label:
            identity_id, _created = store.get_or_create_identity(
                user_id, det["type"], item.label.strip(), environment_id
            )
            if _created:
                _webhook.fire(user_id, environment_id, "identity.created", {
                    "identity_id": identity_id, "label": item.label.strip(),
                    "type": det["type"], "external_ref": None,
                })
        if not identity_id:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": "Provide identity_id or label"})
            continue

        store.label_detection(item.detection_id, user_id, identity_id, environment_id)
        identity = store.get_identity(identity_id, user_id, environment_id)
        _webhook.fire_detection_labeled(
            item.detection_id, user_id, environment_id,
            identity_id=identity_id, label=identity["label"] if identity else None,
        )
        results.append({
            "detection_id": item.detection_id,
            "identity_id": identity_id,
            "label": identity["label"] if identity else None,
            "status": "labeled",
        })
    return results
