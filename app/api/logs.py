"""Admin-only log viewer API — in-memory ring buffer and on-disk log files."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api._responses import ERR_400, ERR_401, ERR_404, ok
from app.core import log_buffer
from app.core.auth import require_admin
from app.core.paths import logs_dir
from app.db import store

router = APIRouter()


@router.get(
    "/api/logs",
    responses={
        **ok({
            "buffer_size": 200,
            "lines": [
                {
                    "level": "INFO",
                    "message": "POST /api/detect/faces 200 OK (142ms)",
                    "timestamp": "2026-01-15T10:30:05Z",
                    "logger": "app.api.detect",
                }
            ],
        }),
        **ERR_401,
    },
)
async def get_logs(
    limit: int | None = Query(None, ge=1, le=log_buffer.MAX_SIZE),
    level: str | None = Query(None),
    user_id: int = Depends(require_admin),
):
    """Return the buffered log lines (oldest first). Optional ?level= and ?limit=."""
    row = store.get_setting("system.log_buffer_size")
    size = int(row["value"]) if row else log_buffer.DEFAULT_SIZE
    return {"buffer_size": size, "lines": log_buffer.get_records(limit, level)}


@router.get(
    "/api/logs/files",
    responses={**ok({"dates": ["2026-01-15", "2026-01-14", "2026-01-13"]}), **ERR_401},
)
async def list_log_files(user_id: int = Depends(require_admin)):
    """List available on-disk log dates, newest first."""
    from app.core import log_files
    return {"dates": log_files.list_dates(logs_dir())}


@router.get(
    "/api/logs/files/{date}/app",
    responses={
        **ok({
            "date": "2026-01-15",
            "total": 842,
            "offset": 0,
            "limit": 200,
            "lines": [
                {"level": "INFO", "message": "Detection complete", "timestamp": "2026-01-15T10:30:05Z"}
            ],
        }),
        **ERR_401,
        **ERR_400,
        **ERR_404,
    },
)
async def read_app_log_file(
    date: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=5000),
    level: str | None = Query(None),
    q: str | None = Query(None),
    user_id: int = Depends(require_admin),
):
    """Return a paginated slice of the app log for the given UTC date."""
    _validate_date(date)
    from app.core import log_files
    return log_files.read_app_log(date, logs_dir(), offset, limit, level, q)


@router.get(
    "/api/logs/files/{date}/activity",
    responses={
        **ok({
            "date": "2026-01-15",
            "total": 47,
            "offset": 0,
            "limit": 200,
            "lines": [
                {"type": "detection", "message": "3 faces detected", "timestamp": "2026-01-15T10:30:00Z"}
            ],
        }),
        **ERR_401,
        **ERR_400,
        **ERR_404,
    },
)
async def read_activity_log_file(
    date: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=5000),
    user_id: int = Depends(require_admin),
):
    """Return a paginated slice of the activity log for the given UTC date."""
    _validate_date(date)
    from app.core import log_files
    return log_files.read_activity_log(date, logs_dir(), offset, limit)


@router.delete(
    "/api/logs/files/{date}",
    status_code=204,
    responses={**ERR_401, **ERR_400},
)
async def delete_log_file(date: str, user_id: int = Depends(require_admin)):
    """Delete both log files (app + activity) for the given UTC date."""
    _validate_date(date)
    from app.core import log_files
    log_files.delete_date(date, logs_dir())


def _validate_date(date: str) -> None:
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format — expected YYYY-MM-DD")
