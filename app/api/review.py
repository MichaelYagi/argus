"""Review queue and casual correction routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core import settings_cache
from app.core.auth import require_auth, require_env_id
from app.db import store


def _auto_enroll(detection_id: int, user_id: int, environment_id: int) -> None:
    """Automatic path only: enroll if the face-detection quality score clears the
    threshold. Used when Argus auto-confirms a high-similarity suggestion with no
    human in the loop, so we avoid promoting low-quality crops unattended.
    """
    threshold = settings_cache.cache.get_or("face.auto_enroll_threshold", 0.92)
    if threshold <= 0:
        return
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det or det["type"] != "face" or det["confidence"] < threshold:
        return
    from app.api.enroll import enroll_from_detection
    enroll_from_detection(det, user_id, environment_id)


def _enroll_confirmed(detection_id: int, user_id: int, environment_id: int) -> None:
    """Human asserted this identity (confirm / reassign / label) — enroll the
    embedding unconditionally so the reference set actually improves. This is
    ground truth, so it is NOT gated on the detection-quality threshold.
    """
    det = store.get_detection(detection_id, user_id, environment_id)
    if det and det["type"] == "face":
        from app.api.enroll import enroll_from_detection
        enroll_from_detection(det, user_id, environment_id)

router = APIRouter()


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

@router.get("/api/review/count")
async def review_count(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return {"count": store.count_pending_review(user_id, environment_id)}


@router.get("/api/review")
async def get_review_queue(
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_review_queue(user_id, cursor=cursor, limit=page_size, environment_id=environment_id)
    has_more = len(rows) > page_size
    items = rows[:page_size]

    model_row = store.get_active_model("face")
    model_id = model_row["id"] if model_row else None

    next_cursor = (
        f"{items[-1]['confidence']}_{items[-1]['id']}" if has_more and items else None
    )

    return {
        "items": [x for x in (_fmt_review_item(r, model_id, user_id, environment_id) for r in items) if x is not None],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------

@router.post("/api/review/{detection_id}/confirm", status_code=200)
async def confirm(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.confirm_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    _enroll_confirmed(detection_id, user_id, environment_id)
    return {"detection_id": detection_id, "review_status": "confirmed"}


@router.post("/api/review/{detection_id}/reject", status_code=200)
async def reject(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.reject_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    # Rejecting clears the identity and drops its reference — refresh the index.
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    return {"detection_id": detection_id, "review_status": "rejected"}


class _ReassignBody(BaseModel):
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.post("/api/review/{detection_id}/reassign", status_code=200)
async def reassign(
    detection_id: int, body: _ReassignBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    identity_id = body.identity_id
    if not identity_id:
        identity_id = store.get_or_create_identity(user_id, "face", body.label.strip(), environment_id)  # type: ignore[union-attr]

    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    store.reassign_detection(detection_id, user_id, identity_id, environment_id)
    _enroll_confirmed(detection_id, user_id, environment_id)  # human named this face — enroll unconditionally
    from app.core import activity_buffer as _ab
    ident = store.get_identity(identity_id, user_id, environment_id)
    _ab.emit("identity", f"Face reassigned to {ident['label'] if ident else identity_id}")
    return {"detection_id": detection_id, "identity_id": identity_id, "review_status": "reassigned"}


class _BulkItem(BaseModel):
    detection_id: int
    action: str  # confirm | reject | reassign
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.post("/api/review/bulk", status_code=200)
async def bulk_review(
    items: list[_BulkItem],
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    results = []
    for item in items:
        if item.action == "confirm":
            store.confirm_detection(item.detection_id, user_id, environment_id)
            _enroll_confirmed(item.detection_id, user_id, environment_id)
            results.append({"detection_id": item.detection_id, "status": "confirmed"})
        elif item.action == "reject":
            store.reject_detection(item.detection_id, user_id, environment_id)
            results.append({"detection_id": item.detection_id, "status": "rejected"})
        elif item.action == "reassign":
            iid = item.identity_id
            if not iid and item.label:
                iid = store.get_or_create_identity(user_id, "face", item.label.strip(), environment_id)
            if not iid:
                results.append({"detection_id": item.detection_id, "status": "error",
                                 "detail": "Provide identity_id or label"})
                continue
            store.reassign_detection(item.detection_id, user_id, iid, environment_id)
            _enroll_confirmed(item.detection_id, user_id, environment_id)
            results.append({"detection_id": item.detection_id, "status": "reassigned",
                             "identity_id": iid})
        else:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": f"Unknown action '{item.action}'"})
    # Rejects (and any reference changes above) may have altered the set — refresh once.
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    from app.core import activity_buffer as _ab
    n_confirmed  = sum(1 for r in results if r.get("status") == "confirmed")
    n_rejected   = sum(1 for r in results if r.get("status") == "rejected")
    n_reassigned = sum(1 for r in results if r.get("status") == "reassigned")
    parts: list[str] = []
    if n_confirmed:
        parts.append(f"{n_confirmed} confirmed")
    if n_rejected:
        parts.append(f"{n_rejected} rejected")
    if n_reassigned:
        parts.append(f"{n_reassigned} reassigned")
    if parts:
        _ab.emit("identity", f"Bulk review: {', '.join(parts)}")
    return results


# ---------------------------------------------------------------------------
# Delete a single detection permanently
# ---------------------------------------------------------------------------

@router.delete("/api/detections/{detection_id}", status_code=204)
async def delete_detection(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.delete_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    # The reference set may have shrunk — refresh the match index.
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)


# ---------------------------------------------------------------------------
# Casual inline correction — shared between faces, objects, and all UI surfaces
# ---------------------------------------------------------------------------

class _LabelBody(BaseModel):
    identity_id: Optional[int] = None
    label: Optional[str] = None


@router.put("/api/detections/{detection_id}/label", status_code=200)
async def label_detection(
    detection_id: int, body: _LabelBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    identity_id = body.identity_id
    if not identity_id:
        identity_id = store.get_or_create_identity(
            user_id, det["type"], body.label.strip(), environment_id  # type: ignore[union-attr]
        )

    store.label_detection(detection_id, user_id, identity_id, environment_id)
    _enroll_confirmed(detection_id, user_id, environment_id)
    identity = store.get_identity(identity_id, user_id, environment_id)
    from app.core import activity_buffer as _ab
    lbl = identity["label"] if identity else str(identity_id)
    kind = "Face" if det["type"] == "face" else "Object"
    _ab.emit("identity", f"{kind} identified as {lbl}")
    return {
        "detection_id": detection_id,
        "identity_id": identity_id,
        "label": identity["label"] if identity else None,
        "review_status": "confirmed",
    }


_BATCH_MAX = 500


class _BatchLabelItem(BaseModel):
    detection_id: int
    identity_id: Optional[int] = None
    label: Optional[str] = None


class _BatchLabelBody(BaseModel):
    items: list[_BatchLabelItem]


@router.post("/api/detections/label", status_code=200)
async def label_detections_batch(
    body: _BatchLabelBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Relabel many detections in one call. Per-item results — one bad item never
    fails the others. Same contract as the single PUT label endpoint."""
    if not body.items:
        raise HTTPException(400, "items is required")
    if len(body.items) > _BATCH_MAX:
        raise HTTPException(400, f"Too many items (max {_BATCH_MAX})")

    results = []
    for item in body.items:
        if not item.identity_id and not (item.label and item.label.strip()):
            results.append({"detection_id": item.detection_id, "ok": False,
                            "error": "Provide identity_id or label"})
            continue
        det = store.get_detection(item.detection_id, user_id, environment_id)
        if not det:
            results.append({"detection_id": item.detection_id, "ok": False, "error": "Detection not found"})
            continue
        identity_id = item.identity_id
        if not identity_id:
            identity_id = store.get_or_create_identity(
                user_id, det["type"], item.label.strip(), environment_id  # type: ignore[union-attr]
            )
        store.label_detection(item.detection_id, user_id, identity_id, environment_id)
        _enroll_confirmed(item.detection_id, user_id, environment_id)
        identity = store.get_identity(identity_id, user_id, environment_id)
        results.append({
            "detection_id": item.detection_id, "ok": True,
            "identity_id": identity_id,
            "label": identity["label"] if identity else None,
        })
    return {"results": results}


class _IdsBody(BaseModel):
    detection_ids: list[int]


@router.post("/api/detections/dismiss", status_code=200)
async def dismiss_detections(
    body: _IdsBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Hide detections from Suggested people without deleting them (sets an ignored flag).
    The rows remain visible on the tag page and in the image's data."""
    if not body.detection_ids:
        raise HTTPException(400, "detection_ids is required")
    if len(body.detection_ids) > _BATCH_MAX:
        raise HTTPException(400, f"Too many items (max {_BATCH_MAX})")
    n = store.dismiss_detections(user_id, body.detection_ids, environment_id)
    return {"dismissed": n}


@router.post("/api/detections/delete", status_code=200)
async def delete_detections(
    body: _IdsBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Permanently delete detections and their crop files. For junk crops you never want."""
    from app.core.paths import crops_dir
    if not body.detection_ids:
        raise HTTPException(400, "detection_ids is required")
    if len(body.detection_ids) > _BATCH_MAX:
        raise HTTPException(400, f"Too many items (max {_BATCH_MAX})")
    crops = store.delete_detections(user_id, body.detection_ids, environment_id)
    removed = 0
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    return {"deleted": len(crops), "crops_removed": removed}


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _fmt_review_item(row: Any, model_id: int | None, user_id: int, environment_id: int) -> dict | None:
    suggested: list[dict] = []
    if model_id and row["embedding"]:
        suggested = _suggested_matches(bytes(row["embedding"]), model_id, user_id, environment_id)

    # If no identity assigned but the top suggestion beats auto-confirm threshold,
    # confirm it now rather than showing "No match found" with an obvious match below.
    if not row["identity_id"] and suggested:
        auto_on  = settings_cache.cache.get_or("face.auto_confirm", True)
        auto_thr = settings_cache.cache.get_or("face.auto_confirm_threshold", 0.80)
        top = suggested[0]
        if auto_on and top["similarity"] >= auto_thr:
            store.label_detection(row["id"], user_id, top["identity_id"], environment_id)
            _auto_enroll(row["id"], user_id, environment_id)
            return None  # remove from queue

    current = (
        {"identity_id": row["identity_id"], "label": row["current_label"]}
        if row["identity_id"] else None
    )
    src_path = row["source_image_path"] if row["source_image_path"] else None
    return {
        "detection_id": row["id"],
        "crop_url": f"/media/crops/{row['crop_path']}",
        "source_image_id": row["source_image_id"],
        "source_image_url": f"/media/sources/{src_path}" if src_path else None,
        "confidence": row["confidence"],
        "detected_at": row["detected_at"],
        "current_identity": current,
        "suggested_matches": suggested,
    }


def _suggested_matches(
    embedding_bytes: bytes, model_id: int, user_id: int, environment_id: int, top_n: int = 5
) -> list[dict]:
    import numpy as np

    from app.core import face_index

    embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
    results   = face_index.search(embedding, user_id, environment_id, threshold=0.0, k=top_n)
    output    = []
    for identity_id, sim in results:
        identity = store.get_identity(identity_id, user_id, environment_id)
        if identity:
            output.append({
                "identity_id": identity_id,
                "label": identity["label"],
                "similarity": round(sim, 4),
            })
    return output
