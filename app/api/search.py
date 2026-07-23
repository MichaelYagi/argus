"""Identity search — FTS5 trigram with LIKE fallback for short queries."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api._responses import ERR_400, ERR_401, ok
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


@router.get(
    "/api/search",
    responses={
        **ok({
            "items": [
                {
                    "id": 3,
                    "label": "Alice",
                    "type": "face",
                    "external_ref": "person_alice",
                    "cover_url": "/media/crops/abc123.jpg",
                    "detection_count": 42,
                }
            ]
        }),
        **ERR_401,
        **ERR_400,
    },
)
async def search_identities(
    q: str = Query(..., min_length=1, max_length=200),
    type: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    rows = store.search_identities(
        user_id, q, environment_id, limit=limit, identity_type=type
    )
    return {
        "items": [
            {
                "id": r["id"],
                "label": r["label"],
                "type": r["type"],
                "external_ref": r["external_ref"],
                "cover_url": f"/media/crops/{r['cover_crop_path']}" if r["cover_crop_path"] else None,
                "detection_count": r["detection_count"],
            }
            for r in rows
        ]
    }
