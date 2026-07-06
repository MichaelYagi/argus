"""Admin-only log viewer API — replays the in-memory ring buffer."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core import log_buffer
from app.core.auth import require_admin
from app.db import store

router = APIRouter()


@router.get("/api/logs")
async def get_logs(
    limit: int | None = Query(None, ge=1, le=log_buffer.MAX_SIZE),
    level: str | None = Query(None),
    user_id: int = Depends(require_admin),
):
    """Return the buffered log lines (oldest first). Optional ?level= and ?limit=."""
    row = store.get_setting("system.log_buffer_size")
    size = int(row["value"]) if row else log_buffer.DEFAULT_SIZE
    return {"buffer_size": size, "lines": log_buffer.get_records(limit, level)}
