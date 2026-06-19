"""Raw sqlite3 access layer. No ORM."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_DB_PATH: Path | None = None


def configure(db_path: str | Path | None) -> None:
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
# Users
# ---------------------------------------------------------------------------

def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, password_hash, 1 if is_admin else 0),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def create_api_key(user_id: int, key_hash: str, label: str) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (user_id, key_hash, label) VALUES (?, ?, ?)",
            (user_id, key_hash, label),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_api_key_user(key_hash: str) -> sqlite3.Row | None:
    """Return {key_id, user_id, username} for an active key hash, or None."""
    with _connect() as conn:
        return conn.execute(
            """SELECT ak.id AS key_id, ak.user_id, u.username
               FROM api_keys ak JOIN users u ON ak.user_id = u.id
               WHERE ak.key_hash = ? AND ak.is_active = 1""",
            (key_hash,),
        ).fetchone()


def touch_api_key(key_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
            (key_id,),
        )


def list_api_keys(user_id: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """SELECT id, label, created_at, last_used_at, is_active
               FROM api_keys WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()


def revoke_api_key(key_id: int, user_id: int) -> bool:
    """Deactivate a key. Returns True if a row was updated (key belonged to user)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Models (shared — no user_id)
# ---------------------------------------------------------------------------

def get_active_model(model_type: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM models WHERE type = ? AND is_active = 1 LIMIT 1",
            (model_type,),
        ).fetchone()


def set_model_active(model_id: int, model_type: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE models SET is_active = 0 WHERE type = ?", (model_type,))
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (model_id,))


# ---------------------------------------------------------------------------
# Identities (per-user)
# ---------------------------------------------------------------------------

def get_or_create_identity(user_id: int, identity_type: str, label: str) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO identities (user_id, type, label) VALUES (?, ?, ?)",
            (user_id, identity_type, label),
        )
        return conn.execute(
            "SELECT id FROM identities WHERE user_id = ? AND type = ? AND label = ?",
            (user_id, identity_type, label),
        ).fetchone()[0]


def get_identity(identity_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM identities WHERE id = ? AND user_id = ?",
            (identity_id, user_id),
        ).fetchone()


# ---------------------------------------------------------------------------
# Source images (per-user)
# ---------------------------------------------------------------------------

def get_or_create_source_image(user_id: int, file_path: str, width: int, height: int) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO source_images (user_id, file_path, width, height) VALUES (?, ?, ?, ?)",
            (user_id, file_path, width, height),
        )
        return conn.execute(
            "SELECT id FROM source_images WHERE user_id = ? AND file_path = ?",
            (user_id, file_path),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Detections (per-user)
# ---------------------------------------------------------------------------

def insert_detection(
    *,
    user_id: int,
    identity_id: int | None,
    source_image_id: int,
    detection_type: str,
    model_id: int | None,
    confidence: float,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    crop_path: str,
) -> int:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, identity_id, source_image_id, detection_type, model_id, confidence,
             bbox_x, bbox_y, bbox_w, bbox_h, crop_path),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_face_embeddings_for_model(model_id: int, user_id: int) -> list[sqlite3.Row]:
    """Return embeddings for the active model scoped to this user's identities."""
    with _connect() as conn:
        return conn.execute(
            """SELECT fe.identity_id, fe.embedding
               FROM face_embeddings fe
               JOIN identities i ON fe.identity_id = i.id
               WHERE fe.model_id = ? AND i.user_id = ?""",
            (model_id, user_id),
        ).fetchall()


# ---------------------------------------------------------------------------
# Settings (shared — no user_id)
# ---------------------------------------------------------------------------

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
