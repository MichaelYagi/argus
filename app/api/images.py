"""Per-image face detection list and batch-tag endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_auth
from app.db import store

router = APIRouter()


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
            }
            for r in rows
        ],
    }


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
