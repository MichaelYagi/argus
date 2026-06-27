"""In-memory ring buffer of recent log records, backing the admin log viewer.

A single logging.Handler keeps the most recent N records (N = system.log_buffer_size)
in a deque and the /api/logs endpoint replays them. Nothing is written to disk; the
buffer is process-local and cleared on restart. Stdlib only — no new dependencies.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

MIN_SIZE = 100
MAX_SIZE = 100000
DEFAULT_SIZE = 500


def clamp(size: int) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size)))


class RingBufferHandler(logging.Handler):
    """Captures formatted log records into a fixed-length deque (oldest drop off)."""

    def __init__(self, capacity: int) -> None:
        super().__init__()
        self._buf: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            # deque.append is atomic in CPython, so no lock needed on the hot path.
            self._buf.append({
                "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            })
        except Exception:  # never let logging raise
            self.handleError(record)

    def get(self, limit: int | None = None, level: str | None = None) -> list[dict]:
        items = list(self._buf)
        if level:
            lv = level.upper()
            items = [e for e in items if e["level"] == lv]
        if limit:
            items = items[-limit:]
        return items

    def resize(self, capacity: int) -> None:
        # deque(maxlen=…) is fixed at creation; rebuild it, preserving recent entries.
        self._buf = deque(self._buf, maxlen=capacity)


_handler: RingBufferHandler | None = None


def install(size: int) -> None:
    """Attach the ring buffer to the root logger (idempotent). Call once at startup."""
    global _handler
    size = clamp(size)
    if _handler is not None:
        _handler.resize(size)
        return

    _handler = RingBufferHandler(size)
    _handler.setLevel(logging.DEBUG)  # capture everything; the viewer filters by level

    root = logging.getLogger()
    root.addHandler(_handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    # uvicorn's loggers set propagate=False, so they never reach the root handler.
    # Attach directly so the viewer mirrors the console: server lifecycle + errors
    # (uvicorn / uvicorn.error) and request access lines (uvicorn.access).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if _handler not in lg.handlers:
            lg.addHandler(_handler)


def resize(size: int) -> None:
    if _handler is not None:
        _handler.resize(clamp(size))


def get_records(limit: int | None = None, level: str | None = None) -> list[dict]:
    return _handler.get(limit, level) if _handler is not None else []
