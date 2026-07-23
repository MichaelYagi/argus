"""Review queue and casual correction routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api._responses import ERR_400, ERR_401, ERR_404, ok
from app.api._utils import delete_crops
from app.core import settings_cache
from app.core import webhook as _webhook
from app.core.auth import require_auth, require_env_id
from app.core.paths import crops_dir
from app.db import store

log = logging.getLogger(__name__)


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


def _enroll_confirmed(detection_id: int, user_id: int, environment_id: int) -> bool:
    """Enroll the embedding for a human-confirmed detection. Returns True if a new
    embedding was added. Ground truth — not gated on the detection-quality threshold.
    """
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        log.warning("enroll requested for detection %d but not found (user=%d env=%d)",
                    detection_id, user_id, environment_id)
        return False
    if det["type"] != "face":
        log.warning("enroll requested for detection %d but type=%s (only face detections can be enrolled)",
                    detection_id, det["type"])
        return False
    if not det["embedding"]:
        log.warning("enroll requested for detection %d but embedding is null — "
                    "was a face model active when this detection was created?", detection_id)
        return False
    from app.api.enroll import enroll_from_detection
    return enroll_from_detection(det, user_id, environment_id)

router = APIRouter()


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

@router.get(
    "/api/review/mismatches",
    responses={
        **ok({
            "items": [
                {
                    "detection_id": 42,
                    "crop_url": "/media/crops/abc.jpg",
                    "source_image_id": 7,
                    "source_image_url": "/media/sources/def.jpg",
                    "confidence": 0.91,
                    "bbox": {"x": 90, "y": 60, "w": 55, "h": 70},
                    "detected_at": "2026-01-15T11:00:00Z",
                    "current_identity": {"identity_id": 3, "label": "Alice"},
                    "similarity": 0.31,
                }
            ],
            "count": 1,
            "threshold": 0.5,
        }),
        **ERR_401,
    },
)
async def get_mismatch_queue(
    threshold: float | None = Query(None, ge=0.0, le=1.0),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Confirmed face detections whose embedding scores poorly against their
    identity's representative — possible mislabels, sorted worst-first. See Mismatches tab."""
    import asyncio
    thr = threshold if threshold is not None else settings_cache.cache.get_or("face.recognition_threshold", 0.5)
    rows = await asyncio.to_thread(store.get_mismatch_detections, user_id, environment_id, float(thr))
    items = [_fmt_mismatch_item(r) for r in rows]
    return {"items": items, "count": len(items), "threshold": round(float(thr), 4)}


@router.post(
    "/api/review/mismatches/{detection_id}/dismiss",
    status_code=200,
    responses={**ok({"detection_id": 42, "mismatch_reviewed": True}), **ERR_401, **ERR_404},
)
async def dismiss_mismatch(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Mark a Mismatches-tab detection as reviewed-and-correct. Suppresses it from
    future mismatch scans without affecting its gallery presence or label."""
    if not store.dismiss_mismatch_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    return {"detection_id": detection_id, "mismatch_reviewed": True}


@router.post(
    "/api/review/mismatches/dismiss",
    status_code=200,
    responses={**ok({"dismissed": 3}), **ERR_401, **ERR_400},
)
async def dismiss_mismatches_batch(
    body: _IdsBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Batch confirm-as-correct for the Mismatches tab. Sets mismatch_reviewed on each
    detection, suppressing them from future scans without affecting gallery or labels."""
    if not body.detection_ids:
        raise HTTPException(400, "detection_ids is required")
    if len(body.detection_ids) > _BATCH_MAX:
        raise HTTPException(400, f"Too many items (max {_BATCH_MAX})")
    n = store.dismiss_mismatch_detections(user_id, body.detection_ids, environment_id)
    return {"dismissed": n}


@router.get(
    "/api/review/count",
    responses={**ok({"count": 14}), **ERR_401},
)
async def review_count(
    has_suggestion: bool | None = Query(None),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return {"count": store.count_pending_review(user_id, environment_id, has_suggestion=has_suggestion)}


@router.get(
    "/api/review",
    responses={
        **ok({
            "items": [
                {
                    "detection_id": 303,
                    "crop_url": "/media/crops/unk001.jpg",
                    "source_image_id": 10,
                    "source_image_url": "/media/sources/jkl012.jpg",
                    "confidence": 0.8514,
                    "bbox": {"x": 90, "y": 60, "w": 55, "h": 70},
                    "detected_at": "2026-01-15T11:00:00Z",
                    "current_identity": None,
                    "suggested_matches": [
                        {"identity_id": 3, "label": "Alice", "similarity": 0.7823},
                    ],
                }
            ],
            "next_cursor": "0.8514_303",
            "has_more": False,
        }),
        **ERR_401,
    },
)
async def get_review_queue(
    cursor: str | None = Query(None),
    limit: int | None = Query(None),
    has_suggestion: bool | None = Query(None),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_review_queue(
        user_id, cursor=cursor, limit=page_size,
        environment_id=environment_id, has_suggestion=has_suggestion,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]

    model_row = store.get_active_model("face")
    model_id = model_row["id"] if model_row else None

    # Auto-confirm pass — DB writes happen here before formatting, not inside the formatter.
    auto_on  = settings_cache.cache.get_or("face.auto_confirm", True)
    auto_thr = settings_cache.cache.get_or("face.auto_confirm_threshold", 0.80)
    kept = []
    for r in items:
        if auto_on and model_id and not r["identity_id"] and r["embedding"] and r["review_status"] != "rejected":
            suggested = _suggested_matches(bytes(r["embedding"]), model_id, user_id, environment_id)
            if suggested and suggested[0]["similarity"] >= auto_thr:
                store.label_detection(r["id"], user_id, suggested[0]["identity_id"], environment_id)
                _auto_enroll(r["id"], user_id, environment_id)
                _webhook.fire_detection_labeled(r["id"], user_id, environment_id,
                                               identity_id=suggested[0]["identity_id"])
                continue
        kept.append(r)

    # Cursor advances past what the client sees. When the entire page was auto-confirmed
    # (kept is empty) we still need a cursor so the client can fetch the next page.
    if kept and has_more:
        next_cursor = f"{kept[-1]['confidence']}_{kept[-1]['id']}"
    elif not kept and has_more and items:
        next_cursor = f"{items[-1]['confidence']}_{items[-1]['id']}"
    else:
        next_cursor = None
    return {
        "items": [_fmt_review_item(r, model_id, user_id, environment_id) for r in kept],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------

@router.post(
    "/api/review/{detection_id}/confirm",
    status_code=200,
    responses={**ok({"detection_id": 303, "review_status": "confirmed"}), **ERR_401, **ERR_404},
)
async def confirm(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.confirm_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    _enroll_confirmed(detection_id, user_id, environment_id)
    _webhook.fire_detection_labeled(detection_id, user_id, environment_id)
    return {"detection_id": detection_id, "review_status": "confirmed"}


@router.post(
    "/api/review/{detection_id}/unidentify",
    status_code=200,
    responses={**ok({"detection_id": 303, "identity_id": None, "review_status": "pending"}), **ERR_401, **ERR_404},
)
async def unidentify(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Clear identity and return to unidentified queue (re-label path).
    Distinct from reject: reject marks the match wrong but keeps the identity link."""
    det = store.get_detection(detection_id, user_id, environment_id)
    if not store.unidentify_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    if det:
        _webhook.fire(user_id, environment_id, "detection.labeled", {
            "detection_id": detection_id,
            "source_image_id": det["source_image_id"],
            "identity_id": None,
            "label": None,
            "type": det["type"],
        })
    return {"detection_id": detection_id, "identity_id": None, "review_status": "pending"}


@router.post(
    "/api/review/{detection_id}/reject",
    status_code=200,
    responses={**ok({"detection_id": 303, "review_status": "rejected"}), **ERR_401, **ERR_404},
)
async def reject(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    det = store.get_detection(detection_id, user_id, environment_id)
    if not store.reject_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found")
    # Rejection drops the face reference — refresh the index.
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    if det:
        _webhook.fire(user_id, environment_id, "detection.labeled", {
            "detection_id": detection_id,
            "source_image_id": det["source_image_id"],
            "identity_id": None,
            "label": None,
            "type": det["type"],
        })
    return {"detection_id": detection_id, "review_status": "rejected"}


@router.post(
    "/api/review/{detection_id}/restore",
    status_code=200,
    responses={**ok({"detection_id": 303, "review_status": "confirmed"}), **ERR_401, **ERR_404},
)
async def restore(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.restore_detection(detection_id, user_id, environment_id):
        raise HTTPException(404, "Detection not found or not in rejected state")
    _enroll_confirmed(detection_id, user_id, environment_id)
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    _webhook.fire_detection_labeled(detection_id, user_id, environment_id)
    return {"detection_id": detection_id, "review_status": "confirmed"}


class _ReassignBody(BaseModel):
    identity_id: int | None = None
    label: str | None = None


@router.post(
    "/api/review/{detection_id}/reassign",
    status_code=200,
    responses={
        **ok({"detection_id": 303, "identity_id": 7, "review_status": "reassigned"}),
        **ERR_401,
        **ERR_404,
        **ERR_400,
    },
)
async def reassign(
    detection_id: int, body: _ReassignBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    if body.identity_id:
        if not store.get_identity(body.identity_id, user_id, environment_id):
            raise HTTPException(404, "Identity not found")
        identity_id = body.identity_id
        _created = False
    else:
        assert body.label
        identity_id, _created = store.get_or_create_identity(user_id, "face", body.label.strip(), environment_id)
        if _created:
            _webhook.fire(user_id, environment_id, "identity.created", {
                "identity_id": identity_id, "label": body.label.strip(),
                "type": "face", "external_ref": None,
            })

    store.reassign_detection(detection_id, user_id, identity_id, environment_id)
    _enroll_confirmed(detection_id, user_id, environment_id)  # human named this face — enroll unconditionally
    from app.core import activity_buffer as _ab
    ident = store.get_identity(identity_id, user_id, environment_id)
    _ab.emit("identity", f"Face reassigned to {ident['label'] if ident else identity_id}")
    _webhook.fire_detection_labeled(
        detection_id, user_id, environment_id,
        identity_id=identity_id, label=ident["label"] if ident else None,
    )
    return {"detection_id": detection_id, "identity_id": identity_id, "review_status": "reassigned"}


class _BulkItem(BaseModel):
    detection_id: int
    action: str  # confirm | reject | reassign
    identity_id: int | None = None
    label: str | None = None


@router.post(
    "/api/review/bulk",
    status_code=200,
    responses={
        **ok([
            {"detection_id": 303, "status": "confirmed"},
            {"detection_id": 304, "status": "rejected"},
        ]),
        **ERR_401,
    },
)
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
            _webhook.fire_detection_labeled(item.detection_id, user_id, environment_id)
            results.append({"detection_id": item.detection_id, "status": "confirmed"})
        elif item.action == "reject":
            det = store.get_detection(item.detection_id, user_id, environment_id)
            store.reject_detection(item.detection_id, user_id, environment_id)
            if det:
                _webhook.fire(user_id, environment_id, "detection.labeled", {
                    "detection_id": item.detection_id,
                    "source_image_id": det["source_image_id"],
                    "identity_id": None,
                    "label": None,
                    "type": det["type"],
                })
            results.append({"detection_id": item.detection_id, "status": "rejected"})
        elif item.action == "reassign":
            iid = item.identity_id
            if iid:
                if not store.get_identity(iid, user_id, environment_id):
                    results.append({"detection_id": item.detection_id, "status": "error",
                                     "detail": "Identity not found"})
                    continue
            elif item.label:
                iid, _created = store.get_or_create_identity(user_id, "face", item.label.strip(), environment_id)
                if _created:
                    _webhook.fire(user_id, environment_id, "identity.created", {
                        "identity_id": iid, "label": item.label.strip(),
                        "type": "face", "external_ref": None,
                    })
            else:
                results.append({"detection_id": item.detection_id, "status": "error",
                                 "detail": "Provide identity_id or label"})
                continue
            store.reassign_detection(item.detection_id, user_id, iid, environment_id)
            _enroll_confirmed(item.detection_id, user_id, environment_id)
            _webhook.fire_detection_labeled(item.detection_id, user_id, environment_id, identity_id=iid)
            results.append({"detection_id": item.detection_id, "status": "reassigned",
                             "identity_id": iid})
        elif item.action == "unidentify":
            det = store.get_detection(item.detection_id, user_id, environment_id)
            store.unidentify_detection(item.detection_id, user_id, environment_id)
            if det:
                _webhook.fire(user_id, environment_id, "detection.labeled", {
                    "detection_id": item.detection_id,
                    "source_image_id": det["source_image_id"],
                    "identity_id": None,
                    "label": None,
                    "type": det["type"],
                })
            results.append({"detection_id": item.detection_id, "status": "unidentified"})
        else:
            results.append({"detection_id": item.detection_id, "status": "error",
                             "detail": f"Unknown action '{item.action}'"})
    # Rejects/unidentifies (and any reference changes above) may have altered the set — refresh once.
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    from app.core import activity_buffer as _ab
    n_confirmed    = sum(1 for r in results if r.get("status") == "confirmed")
    n_rejected     = sum(1 for r in results if r.get("status") == "rejected")
    n_reassigned   = sum(1 for r in results if r.get("status") == "reassigned")
    n_unidentified = sum(1 for r in results if r.get("status") == "unidentified")
    parts: list[str] = []
    if n_confirmed:
        parts.append(f"{n_confirmed} confirmed")
    if n_rejected:
        parts.append(f"{n_rejected} rejected")
    if n_reassigned:
        parts.append(f"{n_reassigned} reassigned")
    if n_unidentified:
        parts.append(f"{n_unidentified} dismissed")
    if parts:
        _ab.emit("identity", f"Bulk review: {', '.join(parts)}")
    return results


# ---------------------------------------------------------------------------
# Get a single detection
# ---------------------------------------------------------------------------

@router.get(
    "/api/detections/{detection_id}",
    responses={
        **ok({
            "detection_id": 101,
            "type": "face",
            "confidence": 0.9832,
            "bbox": {"x": 120, "y": 80, "w": 60, "h": 75},
            "crop_url": "/media/crops/abc123.jpg",
            "identity_id": 3,
            "source_image_id": 7,
            "review_status": "confirmed",
            "detected_at": "2026-01-15T10:30:00Z",
            "attributes": {"age": 32, "gender": "F"},
        }),
        **ERR_401,
        **ERR_404,
    },
)
async def get_detection(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    try:
        attrs = json.loads(det["attributes"]) if det["attributes"] else {}
    except (ValueError, TypeError):
        attrs = {}
    return {
        "detection_id": det["id"],
        "type": det["type"],
        "confidence": det["confidence"],
        "bbox": {"x": det["bbox_x"], "y": det["bbox_y"], "w": det["bbox_w"], "h": det["bbox_h"]},
        "crop_url": f"/media/crops/{det['crop_path']}" if det["crop_path"] else None,
        "identity_id": det["identity_id"],
        "source_image_id": det["source_image_id"],
        "review_status": det["review_status"],
        "detected_at": det["detected_at"],
        "attributes": attrs,
    }


@router.get(
    "/api/detections/{detection_id}/img",
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "JPEG crop image for this detection"},
        **ERR_401,
        **ERR_404,
    },
)
async def get_detection_img(
    detection_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    if not det["crop_path"]:
        raise HTTPException(404, "No crop image for this detection")
    path = crops_dir() / det["crop_path"]
    if not path.exists():
        raise HTTPException(404, "Crop image not found on disk")
    return FileResponse(path, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Delete a single detection permanently
# ---------------------------------------------------------------------------

@router.delete(
    "/api/detections/{detection_id}",
    status_code=204,
    responses={**ERR_401, **ERR_404},
)
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
    _webhook.fire(user_id, environment_id, "detection.deleted",
                  {"detection_ids": [detection_id], "count": 1})


# ---------------------------------------------------------------------------
# Casual inline correction — shared between faces, objects, and all UI surfaces
# ---------------------------------------------------------------------------

class _LabelBody(BaseModel):
    identity_id: int | None = None
    label: str | None = None
    enroll: bool = False


@router.put(
    "/api/detections/{detection_id}/label",
    status_code=200,
    responses={
        **ok({
            "detection_id": 101,
            "identity_id": 3,
            "label": "Alice",
            "review_status": "confirmed",
            "enrolled": False,
        }),
        **ERR_401,
        **ERR_404,
        **ERR_400,
    },
)
async def label_detection(
    detection_id: int, body: _LabelBody,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not body.identity_id and not body.label:
        raise HTTPException(400, "Provide identity_id or label")

    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        raise HTTPException(404, "Detection not found")

    if body.identity_id:
        if not store.get_identity(body.identity_id, user_id, environment_id):
            raise HTTPException(404, "Identity not found")
        identity_id = body.identity_id
    else:
        assert body.label
        identity_id, _created = store.get_or_create_identity(
            user_id, det["type"], body.label.strip(), environment_id
        )
        if _created:
            _webhook.fire(user_id, environment_id, "identity.created", {
                "identity_id": identity_id, "label": body.label.strip(),
                "type": det["type"], "external_ref": None,
            })

    store.label_detection(detection_id, user_id, identity_id, environment_id)
    log.info("label_detection: detection=%d identity=%d enroll=%s", detection_id, identity_id, body.enroll)
    enrolled = False
    if body.enroll:
        enrolled = _enroll_confirmed(detection_id, user_id, environment_id)
    log.info("label_detection: enrolled=%s", enrolled)
    identity = store.get_identity(identity_id, user_id, environment_id)
    from app.core import activity_buffer as _ab
    lbl = identity["label"] if identity else str(identity_id)
    kind = "Face" if det["type"] == "face" else "Object"
    _ab.emit("identity", f"{kind} identified as {lbl}")
    _webhook.fire_detection_labeled(
        detection_id, user_id, environment_id, identity_id=identity_id, label=lbl,
    )
    if det["type"] == "face":
        from app.api.detect import scan_unidentified
        background_tasks.add_task(scan_unidentified, user_id, environment_id)
    return {
        "detection_id": detection_id,
        "identity_id": identity_id,
        "label": identity["label"] if identity else None,
        "review_status": "confirmed",
        "enrolled": enrolled,
    }


_BATCH_MAX = 500


class _BatchLabelItem(BaseModel):
    detection_id: int
    identity_id: int | None = None
    label: str | None = None
    enroll: bool = False


class _BatchLabelBody(BaseModel):
    items: list[_BatchLabelItem]


@router.post(
    "/api/detections/label",
    status_code=200,
    responses={
        **ok({
            "results": [
                {"detection_id": 101, "ok": True, "identity_id": 3, "label": "Alice", "enrolled": False},
                {"detection_id": 202, "ok": False, "error": "Detection not found"},
            ]
        }),
        **ERR_401,
        **ERR_400,
    },
)
async def label_detections_batch(
    body: _BatchLabelBody,
    background_tasks: BackgroundTasks,
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
    has_face = False
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
        if identity_id:
            if not store.get_identity(identity_id, user_id, environment_id):
                results.append({"detection_id": item.detection_id, "ok": False,
                                "error": "Identity not found"})
                continue
        else:
            assert item.label  # loop guard above ensures label is truthy when identity_id is absent
            identity_id, _created = store.get_or_create_identity(
                user_id, det["type"], item.label.strip(), environment_id
            )
            if _created:
                _webhook.fire(user_id, environment_id, "identity.created", {
                    "identity_id": identity_id, "label": item.label.strip(),
                    "type": det["type"], "external_ref": None,
                })
        store.label_detection(item.detection_id, user_id, identity_id, environment_id)
        enrolled = _enroll_confirmed(item.detection_id, user_id, environment_id) if item.enroll else False
        if det["type"] == "face":
            has_face = True
        identity = store.get_identity(identity_id, user_id, environment_id)
        _webhook.fire_detection_labeled(
            item.detection_id, user_id, environment_id,
            identity_id=identity_id, label=identity["label"] if identity else None,
        )
        results.append({
            "detection_id": item.detection_id, "ok": True,
            "identity_id": identity_id,
            "label": identity["label"] if identity else None,
            "enrolled": enrolled,
        })
    if has_face:
        from app.api.detect import scan_unidentified
        background_tasks.add_task(scan_unidentified, user_id, environment_id)
    return {"results": results}


class _IdsBody(BaseModel):
    detection_ids: list[int]


@router.post(
    "/api/detections/dismiss",
    status_code=200,
    responses={**ok({"dismissed": 3}), **ERR_401, **ERR_400},
)
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


@router.post(
    "/api/detections/delete",
    status_code=200,
    responses={**ok({"deleted": 3, "crops_removed": 3}), **ERR_401, **ERR_400},
)
async def delete_detections(
    body: _IdsBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Permanently delete detections and their crop files. For junk crops you never want."""
    if not body.detection_ids:
        raise HTTPException(400, "detection_ids is required")
    if len(body.detection_ids) > _BATCH_MAX:
        raise HTTPException(400, f"Too many items (max {_BATCH_MAX})")
    deleted_ids, crops = store.delete_detections(user_id, body.detection_ids, environment_id)
    removed = delete_crops(crops)
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    if deleted_ids:
        _webhook.fire(user_id, environment_id, "detection.deleted",
                      {"detection_ids": deleted_ids, "count": len(deleted_ids)})
    return {"deleted": len(deleted_ids), "crops_removed": removed}


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _fmt_mismatch_item(row: dict) -> dict:
    src_path = row.get("source_image_path")
    return {
        "detection_id": row["id"],
        "crop_url": f"/media/crops/{row['crop_path']}" if row["crop_path"] else None,
        "source_image_id": row["source_image_id"],
        "source_image_url": f"/media/sources/{src_path}" if src_path else None,
        "confidence": row["confidence"],
        "bbox": {"x": row["bbox_x"], "y": row["bbox_y"], "w": row["bbox_w"], "h": row["bbox_h"]},
        "detected_at": row["detected_at"],
        "current_identity": {"identity_id": row["identity_id"], "label": row["current_label"]},
        "similarity": row["similarity"],
    }


def _fmt_review_item(row: Any, model_id: int | None, user_id: int, environment_id: int) -> dict:
    suggested: list[dict] = []
    # Rejected faces explicitly had a match denied — don't offer new suggestions.
    if model_id and row["embedding"] and row["review_status"] != "rejected":
        suggested = _suggested_matches(bytes(row["embedding"]), model_id, user_id, environment_id)

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
        "bbox": {
            "x": row["bbox_x"], "y": row["bbox_y"],
            "w": row["bbox_w"], "h": row["bbox_h"],
        },
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
