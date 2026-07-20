"""In-memory ring buffer of recent activity events, backing the Activity tab in the Logs modal.

Same capacity bound as the log buffer (system.log_buffer_size). Process-local, cleared on restart.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Callable

import app.core.log_buffer as _lb

_buf: deque[dict] = deque(maxlen=_lb.DEFAULT_SIZE)

# Set by log_files.install() to write activity events to disk.
_file_emit_fn: Callable[[dict], None] | None = None


def install(size: int) -> None:
    global _buf
    _buf = deque(_buf, maxlen=_lb.clamp(size))


def resize(size: int) -> None:
    global _buf
    _buf = deque(_buf, maxlen=_lb.clamp(size))


def emit(category: str, message: str) -> None:
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "category": category,
        "message": message,
    }
    _buf.append(entry)
    if _file_emit_fn is not None:
        try:
            _file_emit_fn(entry)
        except Exception:
            pass


def get_events(limit: int | None = None) -> list[dict]:
    items = list(_buf)
    if limit:
        items = items[-limit:]
    return items
