"""Active engine instances and hot-swap lock.

Swap pattern: load new model weights OUTSIDE the lock, then call swap_*_engine()
to atomically replace the reference. In-flight detect calls keep the old engine
until the swap completes; they are never blocked during weight loading.
"""

from __future__ import annotations

import threading
from typing import Any


class EngineRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._face_engine: Any = None
        self._object_engine: Any = None
        self._tagging_engine: Any = None

    def get_face_engine(self) -> Any:
        with self._lock:
            return self._face_engine

    def get_object_engine(self) -> Any:
        with self._lock:
            return self._object_engine

    def get_tagging_engine(self) -> Any:
        with self._lock:
            return self._tagging_engine

    def swap_face_engine(self, engine: Any) -> None:
        with self._lock:
            self._face_engine = engine

    def swap_object_engine(self, engine: Any) -> None:
        with self._lock:
            self._object_engine = engine

    def swap_tagging_engine(self, engine: Any) -> None:
        with self._lock:
            self._tagging_engine = engine


registry = EngineRegistry()
