"""Per-image face detection list, batch-tag, and reprocess endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api._utils import delete_crops, is_truthy, paginate
from app.core import webhook as _webhook
from app.core.auth import require_auth, require_env_id
from app.core.paths import sources_dir
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
                "source_image_url": f"/media/sources/{r['file_path']}",
                "uploaded_at": r["uploaded_at"],
                "image_tags": json.loads(r["image_tags"]) if r["image_tags"] else [],
            }
            for r in rows
        ],
    }


@router.get("/api/source-images")
async def list_source_images(
    cursor: str | None = Query(None),
    limit: int = Query(40, ge=1, le=200),
    identity_id: int | None = Query(None),
    type: str | None = Query(None, description="Filter by detection type: face or object"),
    since: str | None = Query(None, description="ISO timestamp — images uploaded at or after"),
    until: str | None = Query(None, description="ISO timestamp — images uploaded at or before"),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Paginated list of all processed source images (one row per image), newest first.
    Optional filters: identity_id, type (face/object), since, until."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows = store.list_source_images(
        user_id, cursor=cursor, limit=limit, environment_id=environment_id,
        identity_id=identity_id, detection_type=type, since=since, until=until,
    )
    return paginate(rows, limit, lambda r: {
        "source_image_id": r["id"],
        "external_ref": r["external_ref"],
        "source_image_url": f"/media/sources/{r['file_path']}?h=300",
        "width": r["width"],
        "height": r["height"],
        "detection_count": r["detection_count"],
        "uploaded_at": r["uploaded_at"],
        "image_tags": json.loads(r["image_tags"]) if r["image_tags"] else [],
    }, cursor_fn=lambda r: f"{r['uploaded_at']}_{r['id']}")


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
        "external_ref": src["external_ref"],
        "width": src["width"],
        "height": src["height"],
        "uploaded_at": src["uploaded_at"],
        "source_image_url": f"/media/sources/{src['file_path']}",
        "image_tags": json.loads(src["image_tags"]) if src["image_tags"] else [],
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
    crops = store.delete_source_image(source_image_id, user_id, environment_id)
    if crops is None:
        raise HTTPException(404, "Source image not found")

    removed = delete_crops(crops)

    # References enrolled from the removed crops were dropped too — refresh the index.
    from app.core import face_index
    face_index.rebuild_user(user_id, environment_id)

    return {"source_image_id": source_image_id, "detections_deleted": len(crops),
            "crops_removed": removed}


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
    replace = is_truthy(request.query_params.get("replace", "false"))
    run_async = is_truthy(request.query_params.get("async", "false"))

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

    from app.api.detect import _clear_detections, _run_faces, _run_objects
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
            result["image_tags"] = img_tags
    _webhook.fire(user_id, environment_id, "detection.created", {
        "source_image_id": source_image_id,
        "external_ref": src["external_ref"],
        "type": det_type,
    })
    return result


class _TagItem(BaseModel):
    detection_id: int
    identity_id: int | None = None
    label: str | None = None


@router.post("/api/images/{source_image_id}/tag", status_code=200)
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
        if not identity_id and item.label:
            identity_id, _created = store.get_or_create_identity(
                user_id, det["type"], item.label.strip(), environment_id
            )
            if _created:
                _webhook.fire(user_id, environment_id, "identity.created",
                              {"identity_id": identity_id, "label": item.label.strip(), "type": det["type"]})
        if not identity_id:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": "Provide identity_id or label"})
            continue

        store.label_detection(item.detection_id, user_id, identity_id, environment_id)
        identity = store.get_identity(identity_id, user_id, environment_id)
        results.append({
            "detection_id": item.detection_id,
            "identity_id": identity_id,
            "label": identity["label"] if identity else None,
            "status": "labeled",
        })
    return results
