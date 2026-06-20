"""Review queue and casual correction routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core import settings_cache
from app.core.auth import require_auth
from app.db import store


def _auto_enroll(detection_id: int, user_id: int) -> None:
    """Enroll a detection's embedding if its confidence exceeds the threshold."""
    threshold = settings_cache.cache.get_or("face.auto_enroll_threshold", 0.92)
    if threshold <= 0:
        return
    det = store.get_detection(detection_id, user_id)
    if not det or det["type"] != "face" or det["confidence"] < threshold:
        return
    from app.api.enroll import enroll_from_detection
    enroll_from_detection(det, user_id)

router = APIRouter()


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

@router.get("/api/review/count")
async def review_count(user_id: int = Depends(require_auth)):
    return {"count": store.count_pending_review(user_id)}


@router.get("/api/review")
async def get_review_queue(
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    user_id: int = Depends(require_auth),
):
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_review_queue(user_id, cursor=cursor, limit=page_size)
    has_more = len(rows) > page_size
    items = rows[:page_size]

    model_row = store.get_active_model("face")
    model_id = model_row["id"] if model_row else None

    next_cursor = (
        f"{items[-1]['confidence']}_{items[-1]['id']}" if has_more and items else None
    )

    return {
        "items": [_fmt_review_item(r, model_id, user_id) for r in items],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------

@router.post("/api/review/{detection_id}/confirm", status_code=200)
async def confirm(detection_id: int, user_id: int = Depends(require_auth)):
    if not store.confirm_detection(detection_id, user_id):
        raise HTTPException(404, "Detection not found")
    _auto_enroll(detection_id, user_id)
    return {"detection_id": detection_id, "review_status": "confirmed"}


@router.post("/api/review/{detection_id}/reject", status_code=200)
async def reject(detection_id: int, user_id: int = Depends(require_auth)):
    if not store.reject_detection(detection_id, user_id):
        raise HTTPException(404, "Detection not found")
    return {"detection_id": detection_id, "review_status": "rejected"}


class _ReassignBody(BaseModel):
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.post("/api/review/{detection_id}/reassign", status_code=200)
async def reassign(
    detection_id: int, body: _ReassignBody, user_id: int = Depends(require_auth)
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    identity_id = body.identity_id
    if not identity_id:
        identity_id = store.get_or_create_identity(user_id, "face", body.label.strip())  # type: ignore[union-attr]

    det = store.get_detection(detection_id, user_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    store.reassign_detection(detection_id, user_id, identity_id)
    # Human explicitly named this face — enroll unconditionally (bypass threshold)
    det = store.get_detection(detection_id, user_id)
    if det:
        from app.api.enroll import enroll_from_detection
        enroll_from_detection(det, user_id)
    return {"detection_id": detection_id, "identity_id": identity_id, "review_status": "reassigned"}


class _BulkItem(BaseModel):
    detection_id: int
    action: str  # confirm | reject | reassign
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.post("/api/review/bulk", status_code=200)
async def bulk_review(items: list[_BulkItem], user_id: int = Depends(require_auth)):
    results = []
    for item in items:
        if item.action == "confirm":
            store.confirm_detection(item.detection_id, user_id)
            _auto_enroll(item.detection_id, user_id)
            results.append({"detection_id": item.detection_id, "status": "confirmed"})
        elif item.action == "reject":
            store.reject_detection(item.detection_id, user_id)
            results.append({"detection_id": item.detection_id, "status": "rejected"})
        elif item.action == "reassign":
            iid = item.identity_id
            if not iid and item.label:
                iid = store.get_or_create_identity(user_id, "face", item.label.strip())
            if not iid:
                results.append({"detection_id": item.detection_id, "status": "error",
                                 "detail": "Provide identity_id or label"})
                continue
            store.reassign_detection(item.detection_id, user_id, iid)
            det = store.get_detection(item.detection_id, user_id)
            if det:
                from app.api.enroll import enroll_from_detection
                enroll_from_detection(det, user_id)
            results.append({"detection_id": item.detection_id, "status": "reassigned",
                             "identity_id": iid})
        else:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": f"Unknown action '{item.action}'"})
    return results


# ---------------------------------------------------------------------------
# Delete a single detection permanently
# ---------------------------------------------------------------------------

@router.delete("/api/detections/{detection_id}", status_code=204)
async def delete_detection(detection_id: int, user_id: int = Depends(require_auth)):
    if not store.delete_detection(detection_id, user_id):
        raise HTTPException(404, "Detection not found")


# ---------------------------------------------------------------------------
# Casual inline correction — shared between faces, objects, and all UI surfaces
# ---------------------------------------------------------------------------

class _LabelBody(BaseModel):
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.put("/api/detections/{detection_id}/label", status_code=200)
async def label_detection(
    detection_id: int, body: _LabelBody, user_id: int = Depends(require_auth)
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    det = store.get_detection(detection_id, user_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    identity_id = body.identity_id
    if not identity_id:
        identity_id = store.get_or_create_identity(
            user_id, det["type"], body.label.strip()  # type: ignore[union-attr]
        )

    store.label_detection(detection_id, user_id, identity_id)
    _auto_enroll(detection_id, user_id)
    identity = store.get_identity(identity_id, user_id)
    return {
        "detection_id": detection_id,
        "identity_id": identity_id,
        "label": identity["label"] if identity else None,
        "review_status": "confirmed",
    }


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _fmt_review_item(row: Any, model_id: int | None, user_id: int) -> dict:
    suggested: list[dict] = []
    if model_id and row["embedding"]:
        suggested = _suggested_matches(bytes(row["embedding"]), model_id, user_id)

    current = (
        {"identity_id": row["identity_id"], "label": row["current_label"]}
        if row["identity_id"] else None
    )
    return {
        "detection_id": row["id"],
        "crop_url": f"/media/crops/{row['crop_path']}",
        "source_image_id": row["source_image_id"],
        "confidence": row["confidence"],
        "detected_at": row["detected_at"],
        "current_identity": current,
        "suggested_matches": suggested,
    }


def _suggested_matches(
    embedding_bytes: bytes, model_id: int, user_id: int, top_n: int = 5
) -> list[dict]:
    import numpy as np

    from app.core import face_index

    embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
    results   = face_index.search(embedding, user_id, threshold=0.0, k=top_n)
    output    = []
    for identity_id, sim in results:
        identity = store.get_identity(identity_id, user_id)
        if identity:
            output.append({
                "identity_id": identity_id,
                "label": identity["label"],
                "similarity": round(sim, 4),
            })
    return output
