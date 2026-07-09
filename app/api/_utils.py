"""Shared API utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.core.paths import crops_dir

_log = logging.getLogger(__name__)


def is_truthy(v: Any) -> bool:
    """Return True for string values that represent a truthy flag (1/true/yes/on)."""
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def delete_crops(crops: list[str]) -> int:
    """Delete crop files from disk; return the count successfully removed."""
    removed = 0
    for crop in crops:
        try:
            (crops_dir() / crop).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def delete_sources(sources: list[str]) -> int:
    """Delete source image files from disk; return the count successfully removed."""
    from app.core.paths import sources_dir
    base = sources_dir()
    removed = 0
    for src in sources:
        target = base / src
        try:
            existed = target.exists()
            target.unlink(missing_ok=True)
            if not existed:
                _log.warning("delete_sources: file not found on disk: %s", target)
            removed += 1
        except OSError as exc:
            _log.error("delete_sources: failed to delete %s: %s", target, exc)
    return removed


def fmt_bytes(n: int) -> str:
    """Format a byte count as a human-readable string (B / KB / MB / GB / TB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"  # unreachable but satisfies type checkers


def dir_size(path: Path) -> int:
    """Return total byte size of all files under path; 0 if path does not exist."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def paginate(rows: list, limit: int, serialize: Callable, cursor_fn: Callable | None = None) -> dict:
    """Standard cursor-pagination envelope.

    rows     — query result fetched with limit+1 so has_more can be determined.
    limit    — page size (rows beyond this are proof of has_more).
    serialize — maps a row to the item dict.
    cursor_fn — called with the last item row to produce next_cursor; defaults
                to row["detected_at"] when omitted (detection-list convention).
    """
    has_more = len(rows) > limit
    items = rows[:limit]
    if has_more and items:
        next_cursor = cursor_fn(items[-1]) if cursor_fn else items[-1]["detected_at"]
    else:
        next_cursor = None
    return {"items": [serialize(r) for r in items], "next_cursor": next_cursor, "has_more": has_more}
