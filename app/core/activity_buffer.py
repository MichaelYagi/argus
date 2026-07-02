"""In-memory ring buffer of recent activity events, backing the Activity tab in the Logs modal.

Same capacity bound as the log buffer (system.log_buffer_size). Process-local, cleared on restart.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

import app.core.log_buffer as _lb

_buf: deque[dict] = deque(maxlen=_lb.DEFAULT_SIZE)


def install(size: int) -> None:
    global _buf
    _buf = deque(_buf, maxlen=_lb.clamp(size))


def resize(size: int) -> None:
    global _buf
    _buf = deque(_buf, maxlen=_lb.clamp(size))


def emit(category: str, message: str) -> None:
    _buf.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "category": category,
        "message": message,
    })


def get_events(limit: int | None = None) -> list[dict]:
    items = list(_buf)
    if limit:
        items = items[-limit:]
    return items
