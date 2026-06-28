"""Keyword tagging API.

  POST /api/keywords                       - ad-hoc tagging of an image (no persist)
  GET  /api/images/{source_image_id}/keywords - stored keywords for an ingested image
  GET  /api/images/search?keyword=...      - stored images carrying a keyword
  GET  /api/keywords/vocabulary            - download the global vocabulary (admin)
  PUT  /api/keywords/vocabulary            - replace the vocabulary (admin, bulk)

All routes require X-API-Key; scoped to the caller's (user, environment) and the
active CLIP model. The feature is inert when no CLIP model is active.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core import keyword_index, keyword_jobs
from app.core.auth import require_admin, require_auth, require_env_id
from app.core.engine_registry import registry
from app.core.image_input import acquire_image, open_and_validate
from app.db import store

router = APIRouter()


@router.post("/api/keywords")
async def keywords_for_image(
    request: Request,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Ad-hoc: tag one image (file/image_url/image_base64). Compute-and-return —
    nothing is stored (no source image to attach to)."""
    model = store.get_active_model("clip")
    engine = registry.get_tagging_engine()
    if model is None or engine is None:
        raise HTTPException(409, "No CLIP model is active")
    raw = await acquire_image(request)
    img = open_and_validate(raw)
    keyword_index.build(model["id"])
    vec = engine.embed_image(img)
    pairs = keyword_index.score(vec)
    return {
        "model": model["name"],
        "keywords": [{"keyword": kw, "score": round(float(sc), 4)} for kw, sc in pairs],
    }


@router.get("/api/images/{source_image_id}/keywords")
async def stored_keywords(
    source_image_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Stored keywords for an ingested image (used by the UI's async fill-in)."""
    model = store.get_active_model("clip")
    if model is None:
        return {"keywords": []}
    rows = store.get_image_keywords(source_image_id, model["id"])
    return {
        "model": model["name"],
        "keywords": [
            {"keyword": r["keyword"], "score": round(float(r["score"]), 4)} for r in rows
        ],
    }


@router.get("/api/images/search")
async def search_by_keyword(
    keyword: str = Query(..., min_length=1),
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Stored images tagged with a keyword (exact, case-insensitive), newest first."""
    model = store.get_active_model("clip")
    if model is None:
        return {"items": [], "next_cursor": None, "has_more": False}
    rows = store.search_images_by_keyword(
        user_id, keyword, model["id"], environment_id, cursor, limit,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": [
            {
                "source_image_id": r["source_image_id"],
                "keyword": r["keyword"],
                "score": round(float(r["score"]), 4),
                "source_url": f"/media/sources/{r['file_path']}",
                "width": r["width"],
                "height": r["height"],
            }
            for r in items
        ],
        "next_cursor": str(items[-1]["cursor_id"]) if has_more and items else None,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# Vocabulary management (admin, bulk)
# ---------------------------------------------------------------------------

class _VocabBody(BaseModel):
    words: list[str]


@router.get("/api/keywords/vocabulary")
async def get_vocabulary(user_id: int = Depends(require_admin)):
    words = store.get_vocabulary()
    return {"count": len(words), "version": store.get_vocab_version(), "words": words}


@router.put("/api/keywords/vocabulary")
async def put_vocabulary(body: _VocabBody, user_id: int = Depends(require_admin)):
    """Replace the entire vocabulary (deduplicated server-side) and re-tag the
    library against the new version if a CLIP model is active."""
    if len(body.words) > 100000:
        raise HTTPException(400, "Vocabulary too large (max 100000 entries)")
    count = store.replace_vocabulary(body.words)
    keyword_jobs.trigger_vocab_change()
    return {"count": count, "version": store.get_vocab_version()}


@router.get("/api/keywords/jobs/status")
async def keyword_job_status(user_id: int = Depends(require_admin)):
    """Progress of any running keyword backfill/re-tag job."""
    return keyword_jobs.status()
