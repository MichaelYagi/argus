"""Per-image face detection list, batch-tag, and reprocess endpoints."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.auth import require_auth, require_env_id
from app.core.paths import crops_dir, sources_dir
from app.db import store

router = APIRouter()


@router.get("/api/images")
async def list_images_by_ref(
    external_ref: str = Query(..., description="Opaque caller-owned correlation id"),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Resolve a caller's external_ref to Argus source image(s). Lets a client map its
    own id back to source_image_id without tracking it at upload time."""
    rows = store.list_source_images_by_ref(user_id, external_ref, environment_id)
    return {
        "items": [
            {
                "source_image_id": r["id"],
                "external_ref": r["external_ref"],
                "width": r["width"],
                "height": r["height"],
                "image_url": f"/media/sources/{r['file_path']}",
                "uploaded_at": r["uploaded_at"],
            }
            for r in rows
        ],
    }


@router.get("/api/source-images")
async def list_source_images(
    cursor: Optional[str] = Query(None),
    limit: int = Query(40, ge=1, le=200),
    identity_id: Optional[int] = Query(None),
    type: Optional[str] = Query(None, description="Filter by detection type: face or object"),
    since: Optional[str] = Query(None, description="ISO timestamp — images uploaded at or after"),
    until: Optional[str] = Query(None, description="ISO timestamp — images uploaded at or before"),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Paginated list of all processed source images (one row per image), newest first.
    Optional filters: identity_id, type (face/object), since, until."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be face or object")
    rows = store.list_source_images(
        user_id, cursor=cursor, limit=limit, environment_id=environment_id,
        identity_id=identity_id, detection_type=type, since=since, until=until,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = f"{items[-1]['uploaded_at']}_{items[-1]['id']}" if items and has_more else None
    return {
        "items": [
            {
                "source_image_id": r["id"],
                "image_url": f"/media/sources/{r['file_path']}",
                "width": r["width"],
                "height": r["height"],
                "detection_count": r["detection_count"],
                "uploaded_at": r["uploaded_at"],
            }
            for r in items
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


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


@router.get("/api/images/{source_image_id}/faces")
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
        "width": src["width"],
        "height": src["height"],
        "image_url": f"/media/sources/{src['file_path']}",
        "faces": [
            {
                "detection_id": r["id"],
                "bbox": {"x": r["bbox_x"], "y": r["bbox_y"], "w": r["bbox_w"], "h": r["bbox_h"]},
                "confidence": r["confidence"],
                "identity_id": r["identity_id"],
                "label": r["identity_label"],
                "crop_url": f"/media/crops/{r['crop_path']}",
                "review_status": r["review_status"],
                **_parse_attributes(r),
            }
            for r in rows
        ],
    }


@router.delete("/api/images/{source_image_id}", status_code=200)
async def delete_source_image(
    source_image_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Delete a source image and cascade-delete all its detections (faces + objects).

    Use this before re-detecting a photo to avoid duplicate detections. References
    enrolled from the removed crops are dropped too, so no orphaned references remain.
    """
    crops = store.delete_source_image(source_image_id, user_id)
    if crops is None:
        raise HTTPException(404, "Source image not found")

    removed = 0
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass

    # References enrolled from the removed crops were dropped too — refresh the index.
    from app.core import face_index
    face_index.rebuild_user(user_id, environment_id)

    return {"source_image_id": source_image_id, "detections_deleted": len(crops),
            "crops_removed": removed}


def _is_truthy(val: str) -> bool:
    return val.lower() in ("1", "true", "yes")


@router.post("/api/images/{source_image_id}/reprocess", status_code=200)
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
    replace = _is_truthy(request.query_params.get("replace", "false"))
    run_async = _is_truthy(request.query_params.get("async", "false"))

    source_path = sources_dir() / src["file_path"]
    if not source_path.exists():
        raise HTTPException(409, "Source file no longer on disk — cannot reprocess")
    raw = source_path.read_bytes()

    if run_async:
        from app.api.detect import _run_detection_job
        job_id = store.create_job(user_id, "reprocess", environment_id)
        background_tasks.add_task(
            _run_detection_job, job_id, user_id, environment_id,
            raw, None, replace, det_type, src["external_ref"],
        )
        return {"job_id": job_id, "status": "pending", "source_image_id": source_image_id}

    from app.core.image_input import open_and_validate
    from app.api.detect import _run_faces, _run_objects, _clear_detections

    img = open_and_validate(raw)
    if replace:
        _clear_detections(user_id, environment_id, source_image_id, None if det_type == "all" else det_type)
    result: dict = {"source_image_id": source_image_id}
    if det_type in ("faces", "all"):
        result["faces"] = _run_faces(user_id, environment_id, img, source_image_id)
    if det_type in ("objects", "all"):
        result["objects"] = _run_objects(user_id, environment_id, img, source_image_id)
    return result


class _TagItem(BaseModel):
    detection_id: int
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.post("/api/images/{source_image_id}/tag", status_code=200)
async def tag_image(
    source_image_id: int,
    items: list[_TagItem],
    user_id: int = Depends(require_auth),
):
    src = store.get_source_image(source_image_id, user_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    results = []
    for item in items:
        det = store.get_detection(item.detection_id, user_id)
        if not det or det["source_image_id"] != source_image_id:
            results.append({"detection_id": item.detection_id, "status": "not_found"})
            continue

        identity_id = item.identity_id
        if not identity_id and item.label:
            identity_id = store.get_or_create_identity(user_id, det["type"], item.label.strip())
        if not identity_id:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": "Provide identity_id or label"})
            continue

        store.label_detection(item.detection_id, user_id, identity_id)
        identity = store.get_identity(identity_id, user_id)
        results.append({
            "detection_id": item.detection_id,
            "identity_id": identity_id,
            "label": identity["label"] if identity else None,
            "status": "labeled",
        })
    return results
