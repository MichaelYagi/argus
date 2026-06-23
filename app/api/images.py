"""Per-image face detection list and batch-tag endpoints."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_auth
from app.core.paths import crops_dir
from app.db import store

router = APIRouter()


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
async def image_faces(source_image_id: int, user_id: int = Depends(require_auth)):
    src = store.get_source_image(source_image_id, user_id)
    if not src:
        raise HTTPException(404, "Source image not found")

    rows = store.get_image_detections(source_image_id, user_id, det_type="face")
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
async def delete_source_image(source_image_id: int, user_id: int = Depends(require_auth)):
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
    face_index.rebuild_user(user_id)

    return {"source_image_id": source_image_id, "detections_deleted": len(crops),
            "crops_removed": removed}


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
