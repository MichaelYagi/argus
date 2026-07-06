"""Shared API utilities."""

from __future__ import annotations

from typing import Callable


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
