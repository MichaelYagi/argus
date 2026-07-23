"""Activity feed API — GET /api/activity."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api._responses import ERR_401, ok
from app.core import activity_buffer, log_buffer
from app.core.auth import require_admin
from app.db import store

router = APIRouter()


@router.get(
    "/api/activity",
    responses={
        **ok({
            "buffer_size": 200,
            "events": [
                {
                    "type": "detection",
                    "message": "3 faces detected (photo.jpg)",
                    "timestamp": "2026-01-15T10:30:00Z",
                }
            ],
        }),
        **ERR_401,
    },
)
async def get_activity(
    limit: int | None = Query(None, ge=1, le=log_buffer.MAX_SIZE),
    user_id: int = Depends(require_admin),
):
    row = store.get_setting("system.log_buffer_size")
    size = int(row["value"]) if row else log_buffer.DEFAULT_SIZE
    events = activity_buffer.get_events(limit)
    return {"buffer_size": size, "events": events}
