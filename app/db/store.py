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
        _migrate(conn)
        _seed_models(conn)
        _seed_settings(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for columns not covered by CREATE TABLE IF NOT EXISTS."""
    # Schema column additions
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(detections)")}
    if "embedding" not in existing_cols:
        conn.execute("ALTER TABLE detections ADD COLUMN embedding BLOB")

    existing_user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_approved" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 1")

    existing_user_pref_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "timezone" not in existing_user_pref_cols:
        conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
    if "locale" not in existing_user_pref_cols:
        conn.execute("ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT 'en-US'")

    existing_identity_cols = {r[1] for r in conn.execute("PRAGMA table_info(identities)")}
    if "cover_detection_id" not in existing_identity_cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN cover_detection_id INTEGER REFERENCES detections(id) ON DELETE SET NULL"
        )
    if "representative_embedding" not in existing_identity_cols:
        conn.execute("ALTER TABLE identities ADD COLUMN representative_embedding BLOB")

    # Insert missing seed settings and refresh descriptions on every startup
    existing_keys = {r[0] for r in conn.execute("SELECT key FROM settings")}
    missing = [row for row in _SETTINGS_SEED if row[0] not in existing_keys]
    if missing:
        conn.executemany(
            "INSERT INTO settings (key, value, value_type, category, description) VALUES (?, ?, ?, ?, ?)",
            missing,
        )
    # Always sync descriptions so UI labels stay up to date
    conn.executemany(
        "UPDATE settings SET description = ? WHERE key = ?",
        [(row[4], row[0]) for row in _SETTINGS_SEED],
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password_hash: str, is_admin: bool = False, is_approved: bool = True) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_approved) VALUES (?, ?, ?, ?)",
            (username, password_hash, 1 if is_admin else 0, 1 if is_approved else 0),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_user_preferences(user_id: int, timezone: str, locale: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET timezone = ?, locale = ? WHERE id = ?",
            (timezone, locale, user_id),
        )


def update_password(user_id: int, password_hash: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def get_pending_users() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, created_at FROM users WHERE is_approved = 0 ORDER BY created_at"
        ).fetchall()


def approve_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (user_id,))


def reject_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ? AND is_approved = 0", (user_id,))


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
    """Deactivate a key (keeps the row for audit history)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def delete_api_key(key_id: int, user_id: int) -> bool:
    """Permanently delete a key."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Models (shared — no user_id)
# ---------------------------------------------------------------------------

def list_models(model_type: str | None = None) -> list[sqlite3.Row]:
    with _connect() as conn:
        if model_type:
            return conn.execute(
                "SELECT * FROM models WHERE type = ? ORDER BY type, name", (model_type,)
            ).fetchall()
        return conn.execute("SELECT * FROM models ORDER BY type, name").fetchall()


def get_model(model_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()


def set_model_downloaded(model_id: int, downloaded: bool) -> None:
    with _connect() as conn:
        if downloaded:
            conn.execute("UPDATE models SET is_downloaded = 1 WHERE id = ?", (model_id,))
        else:
            conn.execute(
                "UPDATE models SET is_downloaded = 0, is_active = 0 WHERE id = ?",
                (model_id,),
            )


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

def list_identities(
    user_id: int,
    identity_type: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        sql = "SELECT * FROM identities WHERE user_id = ?"
        params: list = [user_id]
        if identity_type:
            sql += " AND type = ?"
            params.append(identity_type)
        if q:
            sql += " AND label LIKE ?"
            params.append(f"%{q}%")
        if cursor:
            sql += " AND label > ?"
            params.append(cursor)
        sql += " ORDER BY label"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit + 1)  # fetch one extra to determine has_more
        return conn.execute(sql, params).fetchall()


def create_identity(user_id: int, identity_type: str, label: str) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO identities (user_id, type, label) VALUES (?, ?, ?)",
            (user_id, identity_type, label),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def delete_identity(identity_id: int, user_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM identities WHERE id = ? AND user_id = ?",
            (identity_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def delete_all_identities(user_id: int) -> int:
    """Delete all identities and related data for a user. Returns count of identities deleted."""
    with _connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM identities WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM face_embeddings WHERE identity_id IN "
            "(SELECT id FROM identities WHERE user_id = ?)", (user_id,)
        )
        conn.execute("DELETE FROM detections WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM source_images WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM identities WHERE user_id = ?", (user_id,))
        return count


def count_identities(user_id: int, identity_type: str | None = None) -> int:
    with _connect() as conn:
        sql    = "SELECT COUNT(*) FROM identities WHERE user_id = ?"
        params: list = [user_id]
        if identity_type:
            sql += " AND type = ?"
            params.append(identity_type)
        return conn.execute(sql, params).fetchone()[0]


def list_identities_summary(
    user_id: int,
    identity_type: str | None = None,
    cursor: str | None = None,
    limit: int = 30,
) -> list[sqlite3.Row]:
    """Return identities with counts and thumbnail in a single query."""
    with _connect() as conn:
        sql = """SELECT i.*,
                        COUNT(DISTINCT d.id)  AS detection_count,
                        COUNT(DISTINCT fe.id) AS embedding_count,
                        COALESCE(
                          (SELECT dc.crop_path FROM detections dc
                           WHERE dc.id = i.cover_detection_id),
                          (SELECT d2.crop_path FROM detections d2
                           WHERE d2.identity_id = i.id AND d2.user_id = i.user_id
                           ORDER BY d2.detected_at DESC LIMIT 1)
                        ) AS thumbnail_crop
                 FROM identities i
                 LEFT JOIN detections d      ON d.identity_id  = i.id
                 LEFT JOIN face_embeddings fe ON fe.identity_id = i.id
                 WHERE i.user_id = ?"""
        params: list = [user_id]
        if identity_type:
            sql += " AND i.type = ?"
            params.append(identity_type)
        if cursor:
            sql += " AND i.label > ?"
            params.append(cursor)
        sql += " GROUP BY i.id ORDER BY i.label LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def get_identity_with_counts(identity_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            """SELECT i.*,
                      COUNT(DISTINCT d.id)  AS detection_count,
                      COUNT(DISTINCT fe.id) AS embedding_count,
                      COALESCE(
                        (SELECT d_cover.crop_path FROM detections d_cover
                         WHERE d_cover.id = i.cover_detection_id),
                        (SELECT d2.crop_path FROM detections d2
                         WHERE d2.identity_id = i.id AND d2.user_id = i.user_id
                         ORDER BY d2.detected_at DESC LIMIT 1)
                      ) AS thumbnail_crop
               FROM identities i
               LEFT JOIN detections d  ON d.identity_id = i.id
               LEFT JOIN face_embeddings fe ON fe.identity_id = i.id
               WHERE i.id = ? AND i.user_id = ?
               GROUP BY i.id""",
            (identity_id, user_id),
        ).fetchone()


def set_identity_cover(identity_id: int, user_id: int, detection_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            "UPDATE identities SET cover_detection_id = ? WHERE id = ? AND user_id = ?",
            (detection_id, identity_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def get_identity_gallery(
    identity_id: int, user_id: int, cursor: str | None = None, limit: int = 30
) -> list[sqlite3.Row]:
    with _connect() as conn:
        # LEFT JOIN face_embeddings (enrolled references are keyed by crop_path) so each
        # crop carries whether it's currently part of the reference set.
        sql = """SELECT d.id, d.crop_path, d.confidence, d.detected_at, d.review_status,
                        d.source_image_id, fe.id AS embedding_id
                 FROM detections d
                 LEFT JOIN face_embeddings fe
                        ON fe.identity_id = d.identity_id AND fe.source_image_path = d.crop_path
                 WHERE d.identity_id = ? AND d.user_id = ?"""
        params: list = [identity_id, user_id]
        if cursor:
            sql += " AND d.detected_at < ?"
            params.append(cursor)
        sql += " ORDER BY d.detected_at DESC, d.id DESC LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def get_unknown_detections(
    user_id: int,
    detection_type: str | None = None,
    cursor: str | None = None,
    limit: int = 30,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        sql = """SELECT id, type, crop_path, confidence, detected_at
                 FROM detections WHERE user_id = ? AND identity_id IS NULL"""
        params: list = [user_id]
        if detection_type:
            sql += " AND type = ?"
            params.append(detection_type)
        if cursor:
            sql += " AND detected_at < ?"
            params.append(cursor)
        sql += " ORDER BY detected_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def insert_face_embedding(
    identity_id: int,
    model_id: int | None,
    embedding_bytes: bytes,
    source_image_path: str | None = None,
) -> int:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO face_embeddings (identity_id, model_id, embedding, source_image_path)
               VALUES (?, ?, ?, ?)""",
            (identity_id, model_id, embedding_bytes, source_image_path),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


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

def get_source_image(source_image_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM source_images WHERE id = ? AND user_id = ?",
            (source_image_id, user_id),
        ).fetchone()


def get_image_detections(
    source_image_id: int, user_id: int, det_type: str | None = None
) -> list[sqlite3.Row]:
    with _connect() as conn:
        sql = """SELECT d.id, d.type, d.identity_id, d.confidence,
                        d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                        d.crop_path, d.review_status,
                        i.label AS identity_label
                 FROM detections d
                 LEFT JOIN identities i ON d.identity_id = i.id
                 WHERE d.source_image_id = ? AND d.user_id = ?"""
        params: list = [source_image_id, user_id]
        if det_type:
            sql += " AND d.type = ?"
            params.append(det_type)
        sql += " ORDER BY d.id"
        return conn.execute(sql, params).fetchall()


def clear_detections_for_source(
    source_image_id: int, user_id: int, det_type: str | None = None
) -> list[str]:
    """Delete detections for a source image (optionally just one type), keeping the
    source row. Returns removed crop filenames so the caller can delete the files.

    Used by detect's ``replace`` mode to make re-detecting the same image idempotent.
    """
    with _connect() as conn:
        sql = "SELECT crop_path FROM detections WHERE source_image_id = ? AND user_id = ?"
        params: list = [source_image_id, user_id]
        if det_type:
            sql += " AND type = ?"
            params.append(det_type)
        crops = [r["crop_path"] for r in conn.execute(sql, params).fetchall()]

        del_sql = "DELETE FROM detections WHERE source_image_id = ? AND user_id = ?"
        del_params: list = [source_image_id, user_id]
        if det_type:
            del_sql += " AND type = ?"
            del_params.append(det_type)
        conn.execute(del_sql, del_params)
        return crops


def delete_source_image(source_image_id: int, user_id: int) -> list[str] | None:
    """Delete a source image and cascade-delete all its detections (faces + objects).

    Returns the list of crop filenames that were removed (so the caller can delete
    the files on disk), or None if the source image was not found for this user.
    The content-hash source file itself is intentionally left on disk — it may be
    shared with other users/rows.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM source_images WHERE id = ? AND user_id = ?",
            (source_image_id, user_id),
        ).fetchone()
        if not row:
            return None
        crops = [
            r["crop_path"]
            for r in conn.execute(
                "SELECT crop_path FROM detections WHERE source_image_id = ? AND user_id = ?",
                (source_image_id, user_id),
            ).fetchall()
        ]
        # FK ON DELETE CASCADE removes the detections; SET NULL clears cover refs.
        conn.execute(
            "DELETE FROM source_images WHERE id = ? AND user_id = ?",
            (source_image_id, user_id),
        )
        return crops


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
    embedding: bytes | None = None,
    review_status: str = "pending",
) -> int:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path, embedding, review_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, identity_id, source_image_id, detection_type, model_id, confidence,
             bbox_x, bbox_y, bbox_w, bbox_h, crop_path, embedding, review_status),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_detection(detection_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM detections WHERE id = ? AND user_id = ?",
            (detection_id, user_id),
        ).fetchone()


def delete_face_embedding(embedding_id: int, user_id: int) -> bool:
    """Delete a single reference embedding. Verifies ownership via the identity's user_id."""
    with _connect() as conn:
        conn.execute(
            """DELETE FROM face_embeddings
               WHERE id = ?
                 AND identity_id IN (SELECT id FROM identities WHERE user_id = ?)""",
            (embedding_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def remove_reference_by_detection(detection_id: int, user_id: int) -> bool:
    """Remove the reference embedding enrolled from this detection's crop and recompute
    the identity's representative. Returns True if a reference was removed.
    """
    with _connect() as conn:
        det = conn.execute(
            "SELECT identity_id, crop_path FROM detections WHERE id = ? AND user_id = ?",
            (detection_id, user_id),
        ).fetchone()
        if not det or det["identity_id"] is None:
            return False
        identity_id = det["identity_id"]
        conn.execute(
            "DELETE FROM face_embeddings WHERE identity_id = ? AND source_image_path = ?",
            (identity_id, det["crop_path"]),
        )
        removed = conn.execute("SELECT changes()").fetchone()[0] > 0
    if removed:
        model_row = get_active_model("face")
        if model_row:
            compute_and_store_representative(identity_id, model_row["id"])
    return removed


def compute_and_store_representative(identity_id: int, model_id: int) -> None:
    """Compute the mean of all embeddings for (identity, model) and store it."""
    import numpy as np
    with _connect() as conn:
        rows = conn.execute(
            "SELECT embedding FROM face_embeddings WHERE identity_id = ? AND model_id = ?",
            (identity_id, model_id),
        ).fetchall()
        if not rows:
            conn.execute(
                "UPDATE identities SET representative_embedding = NULL WHERE id = ?",
                (identity_id,),
            )
            return
        vecs = [np.frombuffer(bytes(r["embedding"]), dtype=np.float32) for r in rows]
        mean_vec = np.mean(vecs, axis=0).astype(np.float32)
        conn.execute(
            "UPDATE identities SET representative_embedding = ? WHERE id = ?",
            (mean_vec.tobytes(), identity_id),
        )


def get_representative_embeddings(model_id: int, user_id: int) -> list[sqlite3.Row]:
    """Return identities with a representative embedding for this model."""
    with _connect() as conn:
        return conn.execute(
            """SELECT i.id AS identity_id, i.representative_embedding
               FROM identities i
               WHERE i.user_id = ? AND i.representative_embedding IS NOT NULL
                 AND EXISTS (
                   SELECT 1 FROM face_embeddings fe
                   WHERE fe.identity_id = i.id AND fe.model_id = ?
                 )""",
            (user_id, model_id),
        ).fetchall()


def get_face_embeddings_for_model(model_id: int, user_id: int) -> list[sqlite3.Row]:
    """Return embeddings for the active model scoped to this user's identities."""
    with _connect() as conn:
        return conn.execute(
            """SELECT fe.identity_id, fe.embedding, i.label
               FROM face_embeddings fe
               JOIN identities i ON fe.identity_id = i.id
               WHERE fe.model_id = ? AND i.user_id = ?""",
            (model_id, user_id),
        ).fetchall()


def count_pending_review(user_id: int) -> int:
    with _connect() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM detections
               WHERE user_id = ? AND review_status = 'pending' AND type = 'face'""",
            (user_id,),
        ).fetchone()[0]


def get_review_queue(
    user_id: int,
    cursor: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Pending face detections, lowest confidence first. Cursor = 'confidence_id'."""
    with _connect() as conn:
        if cursor:
            try:
                c_conf, c_id = cursor.rsplit("_", 1)
                conf_val = float(c_conf)
                id_val = int(c_id)
            except ValueError:
                conf_val, id_val = 0.0, 0
            rows = conn.execute(
                """SELECT d.id, d.source_image_id, d.model_id, d.confidence,
                          d.crop_path, d.detected_at, d.identity_id, d.embedding,
                          i.label AS current_label
                   FROM detections d
                   LEFT JOIN identities i ON d.identity_id = i.id
                   WHERE d.user_id = ? AND d.review_status = 'pending' AND d.type = 'face'
                     AND (d.confidence > ? OR (d.confidence = ? AND d.id > ?))
                   ORDER BY d.confidence ASC, d.id ASC LIMIT ?""",
                (user_id, conf_val, conf_val, id_val, limit + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.id, d.source_image_id, d.model_id, d.confidence,
                          d.crop_path, d.detected_at, d.identity_id, d.embedding,
                          i.label AS current_label
                   FROM detections d
                   LEFT JOIN identities i ON d.identity_id = i.id
                   WHERE d.user_id = ? AND d.review_status = 'pending' AND d.type = 'face'
                   ORDER BY d.confidence ASC, d.id ASC LIMIT ?""",
                (user_id, limit + 1),
            ).fetchall()
        return rows


def confirm_detection(detection_id: int, user_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            """UPDATE detections SET review_status = 'confirmed', reviewed_at = datetime('now')
               WHERE id = ? AND user_id = ?""",
            (detection_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def reject_detection(detection_id: int, user_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            """UPDATE detections SET review_status = 'rejected', identity_id = NULL,
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ?""",
            (detection_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def reassign_detection(detection_id: int, user_id: int, identity_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            """UPDATE detections SET review_status = 'reassigned', identity_id = ?,
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ?""",
            (identity_id, detection_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def delete_detection(detection_id: int, user_id: int) -> bool:
    """Delete a detection. Also removes any reference embedding enrolled from its crop
    (keeping the reference count consistent) and recomputes the representative. The
    cover photo is cleared automatically via the cover_detection_id ON DELETE SET NULL FK.
    """
    ref_identity = None
    with _connect() as conn:
        row = conn.execute(
            "SELECT identity_id, crop_path FROM detections WHERE id = ? AND user_id = ?",
            (detection_id, user_id),
        ).fetchone()
        if not row:
            return False
        if row["identity_id"] is not None:
            conn.execute(
                "DELETE FROM face_embeddings WHERE identity_id = ? AND source_image_path = ?",
                (row["identity_id"], row["crop_path"]),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                ref_identity = row["identity_id"]
        conn.execute(
            "DELETE FROM detections WHERE id = ? AND user_id = ?",
            (detection_id, user_id),
        )
        deleted = conn.execute("SELECT changes()").fetchone()[0] > 0

    if ref_identity is not None:
        model_row = get_active_model("face")
        if model_row:
            compute_and_store_representative(ref_identity, model_row["id"])
    return deleted


def label_detection(detection_id: int, user_id: int, identity_id: int) -> bool:
    """Casual correction: set identity and mark confirmed."""
    with _connect() as conn:
        conn.execute(
            """UPDATE detections SET identity_id = ?, review_status = 'confirmed',
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ?""",
            (identity_id, detection_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Settings (shared — no user_id)
# ---------------------------------------------------------------------------

def get_setting(key: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT key, value, value_type, category, description, updated_at FROM settings WHERE key = ?",
            (key,),
        ).fetchone()


def get_all_settings() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT key, value, value_type, category, description, updated_at FROM settings ORDER BY category, key"
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
    ("face",   "buffalo_l",          512),
    ("face",   "buffalo_s",          512),
    ("face",   "antelopev2",         512),
    ("object", "yolov8n",            None),
    ("object", "yolov8s",            None),
    ("object", "yolov8m",            None),
    ("object", "yolov8x",            None),
    ("object", "yolo11n",            None),
    ("object", "yolov8s-worldv2",    None),
    ("object", "yolov8m-worldv2",    None),
    ("object", "yolov8l-worldv2",    None),
]

# Default vocabulary for YOLO-World: 80 COCO classes + common extras
_WORLD_CLASSES_DEFAULT = (
    # COCO 80
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,"
    "traffic light,fire hydrant,stop sign,parking meter,bench,"
    "bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,"
    "backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,"
    "sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,"
    "tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,"
    "banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,"
    "donut,cake,chair,couch,potted plant,bed,dining table,toilet,"
    "tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,"
    "toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,"
    "hair drier,toothbrush,"
    # Safety and security
    "gun,rifle,pistol,weapon,sword,face mask,helmet,handcuffs,"
    "security camera,fire extinguisher,police car,ambulance,fire truck,"
    # Events and hazards
    "fire,smoke,explosion,flood,crowd,accident,graffiti,trash,"
    # Extended vehicles
    "van,scooter,tractor,forklift,crane,excavator,helicopter,drone,"
    "go-kart,golf cart,wheelchair,baby stroller,"
    # More animals
    "lion,tiger,leopard,cheetah,wolf,fox,deer,rabbit,squirrel,"
    "raccoon,skunk,beaver,otter,monkey,gorilla,penguin,flamingo,"
    "dolphin,whale,shark,seal,crab,lobster,jellyfish,"
    "turtle,snake,lizard,frog,eagle,owl,parrot,crow,peacock,"
    # Documents and IDs
    "license plate,passport,credit card,badge,barcode,QR code,"
    # Misc useful
    "cigarette,alcohol bottle,ladder,fence,gate,stairs,"
    "fire hydrant,manhole,traffic cone,road sign,street light"
)

_SETTINGS_SEED: list[tuple] = [
    ("face.match_threshold",
     "0.5",   "float",  "face",
     "Match Threshold | Minimum similarity score (0–1) for a face detection to be assigned to an enrolled person"),
    ("face.auto_confirm",
     "true",  "bool",   "face",
     "Auto-Confirm | Automatically confirm high-confidence matches without requiring manual review"),
    ("face.auto_confirm_threshold",
     "0.80",  "float",  "face",
     "Auto-Confirm Threshold | Matches at or above this similarity score are confirmed automatically"),
    ("face.auto_enroll_threshold",
     "0.92",  "float",  "face",
     "Auto-Enroll Threshold | Add confirmed detections to the reference set above this confidence; 0 disables"),
    ("face.detection_confidence",
     "0.6",   "float",  "face",
     "Detection Confidence | Minimum confidence for a face region to be reported at all"),
    ("face.min_face_size",
     "40",    "int",    "face",
     "Minimum Face Size | Faces smaller than this many pixels wide or tall are ignored"),
    ("object.detection_confidence",
     "0.5",   "float",  "object",
     "Detection Confidence | Minimum confidence for a detected object to be reported"),
    ("object.iou_threshold",
     "0.45",  "float",  "object",
     "Overlap Threshold | How much bounding boxes can overlap before the weaker one is suppressed (NMS)"),
    ("object.classes_enabled",
     "*",     "string", "object",
     "Enabled Classes | Which COCO object classes to detect; * means all 80, or enter a comma-separated list"),
    ("object.world_classes",
     _WORLD_CLASSES_DEFAULT, "string", "object",
     "YOLO-World Vocabulary | Classes to detect with a YOLO-World model; edit to add or remove"),
    ("system.gallery_page_size",
     "30",    "int",    "system",
     "Gallery Page Size | Number of crop thumbnails loaded per scroll batch in galleries"),
    ("system.save_unknown_detections",
     "true",  "bool",   "system",
     "Save Unknown Detections | Keep detections that didn't match any enrolled person or object class"),
    ("system.crop_padding",
     "0.2",   "float",  "system",
     "Crop Padding | Extra space added around a detection's bounding box before saving the thumbnail"),
    ("system.url_fetch_timeout_seconds",
     "10",    "int",    "system",
     "URL Timeout | Seconds to wait when downloading an image from a URL"),
    ("system.url_fetch_max_size_mb",
     "25",    "int",    "system",
     "URL Size Limit | Maximum image size in MB when fetching from a URL; larger images are rejected"),
    ("system.use_gpu",
     "true",  "bool",   "system",
     "Use GPU | Enable GPU inference when a CUDA device is available; disable to force CPU"),
]


def get_settings_defaults() -> dict[str, str]:
    """Return {key: default_value} from seed data — used by the reset endpoint."""
    return {row[0]: row[1] for row in _SETTINGS_SEED}


def _seed_models(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO models (type, name, embedding_dim) VALUES (?, ?, ?)",
        _MODEL_SEED,
    )


def _seed_settings(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO settings (key, value, value_type, category, description)
           VALUES (?, ?, ?, ?, ?)""",
        _SETTINGS_SEED,
    )
