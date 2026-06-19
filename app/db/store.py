"""Raw sqlite3 access layer. No ORM."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_DB_PATH: Path | None = None


def configure(db_path: "str | Path | None") -> None:
    """Override the DB file path. Pass None to revert to DB_PATH env var / default."""
    global _DB_PATH
    _DB_PATH = Path(db_path) if db_path is not None else None


def _resolved_db_path() -> Path:
    if _DB_PATH is not None:
        return _DB_PATH
    return Path(os.environ.get("DB_PATH", "data/argus.db"))


@contextmanager
def _connect():
    path = _resolved_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Apply schema idempotently and seed reference data if tables are empty."""
    with _connect() as conn:
        # executescript issues an implicit COMMIT first, then runs all DDL
        conn.executescript(_SCHEMA_PATH.read_text())
        _seed_models(conn)
        _seed_settings(conn)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_MODEL_SEED: list[tuple] = [
    ("face",   "buffalo_l",   512),
    ("face",   "buffalo_s",   512),
    ("face",   "antelopev2",  512),
    ("object", "yolov8n",     None),
    ("object", "yolov8s",     None),
    ("object", "yolov8m",     None),
    ("object", "yolov8x",     None),
    ("object", "yolo11n",     None),
]

_SETTINGS_SEED: list[tuple] = [
    ("face.match_threshold",             "0.5",   "float",  "face",   "min cosine similarity to count as a match"),
    ("face.detection_confidence",        "0.6",   "float",  "face",   "min RetinaFace detection confidence"),
    ("face.min_face_size",               "40",    "int",    "face",   "ignore faces smaller than N px"),
    ("object.detection_confidence",      "0.5",   "float",  "object", "min YOLO confidence"),
    ("object.iou_threshold",             "0.45",  "float",  "object", "NMS overlap threshold"),
    ("object.classes_enabled",           "*",     "string", "object", "comma list or * for all COCO classes"),
    ("system.gallery_page_size",         "30",    "int",    "system", "infinite scroll batch size"),
    ("system.save_unknown_detections",   "true",  "bool",   "system", "log unmatched faces/objects to gallery"),
    ("system.crop_padding",              "0.2",   "float",  "system", "padding % around bbox when saving crop"),
    ("system.url_fetch_timeout_seconds", "10",    "int",    "system", "max wait when fetching an image_url"),
    ("system.url_fetch_max_size_mb",     "25",    "int",    "system", "reject fetched images larger than this"),
    ("system.use_gpu",                   "true",  "bool",   "system", "use GPU if available; false forces CPU"),
]


def get_all_settings() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT key, value, value_type, category, description FROM settings ORDER BY category, key"
        ).fetchall()


def update_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?",
            (value, key),
        )


def _seed_models(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] > 0:
        return
    conn.executemany(
        "INSERT INTO models (type, name, embedding_dim) VALUES (?, ?, ?)",
        _MODEL_SEED,
    )


def _seed_settings(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] > 0:
        return
    conn.executemany(
        "INSERT INTO settings (key, value, value_type, category, description) VALUES (?, ?, ?, ?, ?)",
        _SETTINGS_SEED,
    )
