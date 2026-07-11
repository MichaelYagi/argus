"""In-process typed settings cache. Loaded from DB on startup, refreshed on PUT /api/settings/*."""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.db import store

logger = logging.getLogger(__name__)


def coerce_setting(value: str, value_type: str) -> Any:
    if value_type == "float":
        return float(value)
    if value_type == "int":
        return int(value)
    if value_type == "bool":
        return value.lower() == "true"
    return value


class SettingsCache:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        """Reload all settings from DB. Call at startup and after any PUT /api/settings/*."""
        rows = store.get_all_settings()
        with self._lock:
            self._data = {r["key"]: coerce_setting(r["value"], r["value_type"]) for r in rows}
        logger.debug("settings cache loaded: %d keys", len(self._data))

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def get_or(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, raw_value: str, value_type: str) -> None:
        """Update a single entry without a full reload. Call after writing to DB."""
        coerced = coerce_setting(raw_value, value_type)
        with self._lock:
            self._data[key] = coerced
        logger.debug("settings cache updated: %s = %r", key, coerced)

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)


cache = SettingsCache()
