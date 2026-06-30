"""Identity CRUD, gallery, and unknown-detections routes."""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core import settings_cache
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


class _CreateBody(BaseModel):
    label: str
    type: str
    external_ref: str | None = None


# ---------------------------------------------------------------------------
# Identity CRUD
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def stats(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Aggregate counts for the dashboard — single round-trip."""
    return {
        "people":         store.count_identities(user_id, identity_type="face", environment_id=environment_id),
        "objects":        store.count_identities(user_id, identity_type="object", environment_id=environment_id),
        "images":         store.count_source_images(user_id, environment_id),
        "pending_review": store.count_pending_review(user_id, environment_id),
    }


@router.get("/api/identities/summary")
async def identities_summary(
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=1000),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Identities with counts + thumbnail in one query — for paginated dashboard grid."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows     = store.list_identities_summary(
        user_id, identity_type=type, cursor=cursor, limit=limit, environment_id=environment_id
    )
    has_more = len(rows) > limit
    items    = rows[:limit]
    total    = store.count_identities(user_id, identity_type=type, environment_id=environment_id)
    return {
        "items": [
            {
                **_fmt(r),
                "detection_count": r["detection_count"],
                "embedding_count": r["embedding_count"],
                "thumbnail_url": f"/media/crops/{r['thumbnail_crop']}" if r["thumbnail_crop"] else None,
            }
            for r in items
        ],
        "next_cursor": items[-1]["label"] if has_more and items else None,
        "has_more": has_more,
        "total": total,
    }


@router.get("/api/identities")
async def list_identities(
    type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    external_ref: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows = store.list_identities(
        user_id, identity_type=type, q=q, cursor=cursor, limit=limit,
        environment_id=environment_id, external_ref=external_ref,
    )

    if limit is None:
        return {"items": [_fmt(r) for r in rows]}

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["label"] if has_more and items else None
    return {
        "items": [_fmt(r) for r in items],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.get("/api/identities/{identity_id}")
async def get_identity(
    identity_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    row = store.get_identity_with_counts(identity_id, user_id, environment_id)
    if not row:
        raise HTTPException(404, "Identity not found")
    result = _fmt(row)
    result["detection_count"] = row["detection_count"]
    result["embedding_count"] = row["embedding_count"]
    crop = row["thumbnail_crop"]
    result["thumbnail_url"] = f"/media/crops/{crop}" if crop else None
    return result


@router.post("/api/identities", status_code=201)
async def create_identity(
    body: _CreateBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if body.type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "label is required")
    ext = (body.external_ref or "").strip() or None
    try:
        identity_id = store.create_identity(user_id, body.type, label, environment_id, ext)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Identity '{label}' ({body.type}) already exists")
    return {"id": identity_id, "type": body.type, "label": label, "external_ref": ext}


class _RenameBody(BaseModel):
    label: str


@router.put("/api/identities/{identity_id}", status_code=200)
async def rename_identity(
    identity_id: int,
    body: _RenameBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "label is required")
    try:
        ok = store.rename_identity(identity_id, user_id, label, environment_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Identity '{label}' already exists in this environment")
    if not ok:
        raise HTTPException(404, "Identity not found")
    return {"id": identity_id, "label": label}


class _ExternalRefBody(BaseModel):
    external_ref: str | None = None


@router.put("/api/identities/{identity_id}/external_ref", status_code=200)
async def set_external_ref(
    identity_id: int,
    body: _ExternalRefBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    ext = (body.external_ref or "").strip() or None
    if not store.set_identity_external_ref(identity_id, user_id, ext, environment_id):
        raise HTTPException(404, "Identity not found")
    return {"id": identity_id, "external_ref": ext}


class _CoverBody(BaseModel):
    detection_id: int


@router.put("/api/identities/{identity_id}/cover", status_code=200)
async def set_cover(
    identity_id: int,
    body: _CoverBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.set_identity_cover(identity_id, user_id, body.detection_id, environment_id):
        raise HTTPException(404, "Identity not found")
    return {"identity_id": identity_id, "cover_detection_id": body.detection_id}


@router.delete("/api/identities/{identity_id}", status_code=204)
async def delete_identity(
    identity_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    from app.core.paths import crops_dir
    deleted, crops = store.delete_identity(identity_id, user_id, environment_id)
    if not deleted:
        raise HTTPException(404, "Identity not found")
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
        except OSError:
            pass
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)


class _MergeBody(BaseModel):
    into: int  # target identity_id


@router.post("/api/identities/{identity_id}/merge", status_code=200)
async def merge_identity(
    identity_id: int,
    body: _MergeBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Merge identity {identity_id} into {body.into}: all detections and embeddings are
    reassigned to the target, then the source identity is deleted."""
    if identity_id == body.into:
        raise HTTPException(400, "Source and target identity must differ")
    ok = store.merge_identities(identity_id, body.into, user_id, environment_id)
    if not ok:
        raise HTTPException(404, "One or both identities not found")
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    return {"merged_into": body.into, "deleted": identity_id}


@router.delete("/api/identities", status_code=200)
async def delete_all_identities(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    from app.core.paths import crops_dir
    count, crops = store.delete_all_identities(user_id, environment_id)
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
        except OSError:
            pass
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id, environment_id)
    return {"deleted": count}


# ---------------------------------------------------------------------------
# Galleries
# ---------------------------------------------------------------------------

@router.get("/api/identities/{identity_id}/gallery")
async def identity_gallery(
    identity_id: int,
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    enrolled: Optional[bool] = Query(None),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.get_identity(identity_id, user_id, environment_id):
        raise HTTPException(404, "Identity not found")
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_identity_gallery(
        identity_id, user_id, cursor=cursor, limit=page_size,
        environment_id=environment_id, enrolled=enrolled,
    )

    # Similarity shown per crop follows the active matching method.
    if settings_cache.cache.get_or("face.match_strategy", "best") != "average":
        refs = store.get_identity_reference_blobs(identity_id, user_id, environment_id)
        sim_fn = lambda emb: store.best_cosine(emb, refs)  # noqa: E731
    else:
        rep = store.get_representative_embedding(identity_id, user_id, environment_id)
        sim_fn = lambda emb: store.cosine_similarity(emb, rep)  # noqa: E731

    return _paginate(rows, page_size, lambda r: {
        "detection_id": r["id"],
        "source_image_id": r["source_image_id"],
        "source_image_url": f"/media/sources/{r['source_image_path']}" if r["source_image_path"] else None,
        "crop_url": f"/media/crops/{r['crop_path']}",
        "confidence": r["confidence"],
        "similarity": sim_fn(r["embedding"]),
        "detected_at": r["detected_at"],
        "review_status": r["review_status"],
        "enrolled": r["embedding_id"] is not None,
    })


class _DetectionQueryBody(BaseModel):
    detection_ids: list[int]


@router.post("/api/detections/query", status_code=200)
async def query_detections(
    body: _DetectionQueryBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Batch read: fetch current state of many detections in one call. For clients
    reconciling stored records against Argus without N round-trips. Unknown/foreign
    ids are simply absent from the result."""
    if not body.detection_ids:
        raise HTTPException(400, "detection_ids is required")
    if len(body.detection_ids) > 500:
        raise HTTPException(400, "Too many ids (max 500)")
    rows = store.get_detections_by_ids(user_id, body.detection_ids, environment_id)
    return {
        "items": [
            {
                "detection_id": r["id"],
                "type": r["type"],
                "identity_id": r["identity_id"],
                "label": r["identity_label"],
                "identity_external_ref": r["identity_external_ref"],
                "source_image_id": r["source_image_id"],
                "source_external_ref": r["source_external_ref"],
                "confidence": r["confidence"],
                "review_status": r["review_status"],
                "bbox": {"x": r["bbox_x"], "y": r["bbox_y"], "w": r["bbox_w"], "h": r["bbox_h"]},
                "crop_url": f"/media/crops/{r['crop_path']}",
                "detected_at": r["detected_at"],
            }
            for r in rows
        ],
    }


class _SearchBody(BaseModel):
    identity_ids: Optional[list[int]] = None
    type: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    confidence_min: Optional[float] = None
    cursor: Optional[str] = None
    limit: int = 40


@router.post("/api/images/search", status_code=200)
async def search_images(
    body: _SearchBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Find source images matching all supplied filters.

    identity_ids uses AND semantics — every listed identity must appear in the image.
    type filters by detection type (face/object). since/until are ISO timestamps.
    confidence_min filters by minimum detection confidence.
    """
    if body.type and body.type not in ("face", "object"):
        raise HTTPException(400, "type must be face or object")
    if body.limit < 1 or body.limit > 200:
        raise HTTPException(400, "limit must be 1–200")
    rows = store.search_source_images(
        user_id,
        environment_id=environment_id,
        identity_ids=body.identity_ids or None,
        detection_type=body.type,
        since=body.since,
        until=body.until,
        confidence_min=body.confidence_min,
        cursor=body.cursor,
        limit=body.limit,
    )
    has_more = len(rows) > body.limit
    items = rows[:body.limit]
    next_cursor = (
        f"{items[-1]['uploaded_at']}_{items[-1]['source_image_id']}"
        if items and has_more else None
    )
    return {
        "items": [
            {
                "source_image_id": r["source_image_id"],
                "source_image_url": f"/media/sources/{r['file_path']}",
                "external_ref": r["external_ref"],
                "width": r["width"],
                "height": r["height"],
                "uploaded_at": r["uploaded_at"],
            }
            for r in items
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.get("/api/detections/unknown")
async def unknown_detections(
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_unknown_detections(
        user_id, detection_type=type, cursor=cursor, limit=page_size, environment_id=environment_id
    )
    return _paginate(rows, page_size, lambda r: {
        "detection_id": r["id"],
        "type": r["type"],
        "crop_url": f"/media/crops/{r['crop_path']}",
        "confidence": r["confidence"],
        "detected_at": r["detected_at"],
        "source_image_id": r["source_image_id"],
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine(emb: bytes | None, rep: bytes | None) -> float | None:
    return store.cosine_similarity(emb, rep)


def _fmt(row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "label": row["label"],
        "external_ref": _safe(row, "external_ref"),
        "created_at": row["created_at"],
    }


def _safe(row, key):
    """Tolerant column access — newly-migrated columns may be absent on some rows."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _paginate(rows: list, limit: int, serialize) -> dict:
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["detected_at"] if has_more and items else None
    return {"items": [serialize(r) for r in items], "next_cursor": next_cursor, "has_more": has_more}
