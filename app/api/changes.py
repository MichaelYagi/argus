"""Change feed — GET /api/changes?since=<cursor> for delta sync.

Clients poll this to learn what changed (identities/detections created, relabeled,
deleted) without re-scanning galleries. The returned `next_cursor` is the id to pass
as `since` on the next poll. Generic recognition events — no client-specific shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api._responses import ERR_401, ok
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


@router.get(
    "/api/changes",
    responses={
        **ok({
            "items": [
                {
                    "id": 42,
                    "entity_type": "detection",
                    "entity_id": 101,
                    "action": "labeled",
                    "external_ref": "img_abc123",
                    "created_at": "2026-01-15T10:30:00Z",
                }
            ],
            "next_cursor": 42,
            "has_more": False,
        }),
        **ERR_401,
    },
)
async def get_changes(
    since: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    rows = store.list_changes(user_id, since=since, limit=limit, environment_id=environment_id)
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["id"] if items else since
    return {
        "items": [
            {
                "id": r["id"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "action": r["action"],
                "external_ref": r["external_ref"],
                "created_at": r["created_at"],
            }
            for r in items
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
