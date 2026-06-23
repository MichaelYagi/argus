"""Identity CRUD, gallery, and unknown-detections routes."""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core import settings_cache
from app.core.auth import require_auth
from app.db import store

router = APIRouter()


class _CreateBody(BaseModel):
    label: str
    type: str


# ---------------------------------------------------------------------------
# Identity CRUD
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def stats(user_id: int = Depends(require_auth)):
    """Aggregate counts for the dashboard — single round-trip."""
    return {
        "people":         store.count_identities(user_id, identity_type="face"),
        "objects":        store.count_identities(user_id, identity_type="object"),
        "images":         store.count_source_images(user_id),
        "pending_review": store.count_pending_review(user_id),
    }


@router.get("/api/identities/summary")
async def identities_summary(
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    user_id: int = Depends(require_auth),
):
    """Identities with counts + thumbnail in one query — for paginated dashboard grid."""
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows     = store.list_identities_summary(user_id, identity_type=type, cursor=cursor, limit=limit)
    has_more = len(rows) > limit
    items    = rows[:limit]
    total    = store.count_identities(user_id, identity_type=type)
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
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    user_id: int = Depends(require_auth),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows = store.list_identities(user_id, identity_type=type, q=q, cursor=cursor, limit=limit)

    if limit is None:
        return [_fmt(r) for r in rows]

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["label"] if has_more and items else None
    return {
        "items": [_fmt(r) for r in items],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.get("/api/identities/{identity_id}")
async def get_identity(identity_id: int, user_id: int = Depends(require_auth)):
    row = store.get_identity_with_counts(identity_id, user_id)
    if not row:
        raise HTTPException(404, "Identity not found")
    result = _fmt(row)
    result["detection_count"] = row["detection_count"]
    result["embedding_count"] = row["embedding_count"]
    crop = row["thumbnail_crop"]
    result["thumbnail_url"] = f"/media/crops/{crop}" if crop else None
    return result


@router.post("/api/identities", status_code=201)
async def create_identity(body: _CreateBody, user_id: int = Depends(require_auth)):
    if body.type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "label is required")
    try:
        identity_id = store.create_identity(user_id, body.type, label)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Identity '{label}' ({body.type}) already exists")
    return {"id": identity_id, "type": body.type, "label": label}


class _CoverBody(BaseModel):
    detection_id: int


@router.put("/api/identities/{identity_id}/cover", status_code=200)
async def set_cover(identity_id: int, body: _CoverBody, user_id: int = Depends(require_auth)):
    if not store.set_identity_cover(identity_id, user_id, body.detection_id):
        raise HTTPException(404, "Identity not found")
    return {"identity_id": identity_id, "cover_detection_id": body.detection_id}


@router.delete("/api/identities/{identity_id}", status_code=204)
async def delete_identity(identity_id: int, user_id: int = Depends(require_auth)):
    if not store.delete_identity(identity_id, user_id):
        raise HTTPException(404, "Identity not found")
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id)


@router.delete("/api/identities", status_code=200)
async def delete_all_identities(user_id: int = Depends(require_auth)):
    count = store.delete_all_identities(user_id)
    from app.core import face_index as _fi
    _fi.rebuild_user(user_id)
    return {"deleted": count}


# ---------------------------------------------------------------------------
# Galleries
# ---------------------------------------------------------------------------

@router.get("/api/identities/{identity_id}/gallery")
async def identity_gallery(
    identity_id: int,
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    user_id: int = Depends(require_auth),
):
    if not store.get_identity(identity_id, user_id):
        raise HTTPException(404, "Identity not found")
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_identity_gallery(identity_id, user_id, cursor=cursor, limit=page_size)

    # Similarity shown per crop follows the active matching method.
    if settings_cache.cache.get_or("face.match_strategy", "best") != "average":
        refs = store.get_identity_reference_blobs(identity_id, user_id)
        sim_fn = lambda emb: store.best_cosine(emb, refs)  # noqa: E731
    else:
        rep = store.get_representative_embedding(identity_id, user_id)
        sim_fn = lambda emb: store.cosine_similarity(emb, rep)  # noqa: E731

    return _paginate(rows, page_size, lambda r: {
        "detection_id": r["id"],
        "source_image_id": r["source_image_id"],
        "crop_url": f"/media/crops/{r['crop_path']}",
        "confidence": r["confidence"],
        "similarity": sim_fn(r["embedding"]),
        "detected_at": r["detected_at"],
        "review_status": r["review_status"],
        "enrolled": r["embedding_id"] is not None,
    })


@router.get("/api/detections/unknown")
async def unknown_detections(
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    user_id: int = Depends(require_auth),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    page_size = limit or settings_cache.cache.get_or("system.gallery_page_size", 30)
    rows = store.get_unknown_detections(user_id, detection_type=type, cursor=cursor, limit=page_size)
    return _paginate(rows, page_size, lambda r: {
        "detection_id": r["id"],
        "type": r["type"],
        "crop_url": f"/media/crops/{r['crop_path']}",
        "confidence": r["confidence"],
        "detected_at": r["detected_at"],
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
        "created_at": row["created_at"],
    }


def _paginate(rows: list, limit: int, serialize) -> dict:
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["detected_at"] if has_more and items else None
    return {"items": [serialize(r) for r in items], "next_cursor": next_cursor, "has_more": has_more}
