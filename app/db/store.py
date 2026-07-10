"""Raw sqlite3 access layer. No ORM."""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_DB_PATH: Path | None = None


Row = sqlite3.Row


class DuplicateError(Exception):
    """Raised when an INSERT or UPDATE violates a uniqueness constraint."""


def configure(db_path: str | Path | None) -> None:
    """Override the DB file path. Pass None to revert to DB_PATH env var / default."""
    global _DB_PATH
    _DB_PATH = Path(db_path) if db_path is not None else None


def _resolved_db_path() -> Path:
    if _DB_PATH is not None:
        return _DB_PATH
    data = os.environ.get("DATA_PATH", "data")
    return Path(os.environ.get("DB_PATH", f"{data}/argus.db"))


@contextmanager
def _connect():
    path = _resolved_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
    # Environments: create table (handled by schema.sql), add column to data tables,
    # create default environment per user, backfill existing rows.
    for tbl, col in [
        ("api_keys",       "environment_id"),
        ("identities",     "environment_id"),
        ("source_images",  "environment_id"),
        ("detections",     "environment_id"),
        ("face_embeddings","environment_id"),
        ("jobs",           "environment_id"),
    ]:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")

    # Ensure every user has at least one environment — but only seed 'default' for
    # users who have NONE, so a deliberately-deleted 'default' isn't resurrected on
    # restart when the user still has other environments.
    user_ids = [r[0] for r in conn.execute("SELECT id FROM users").fetchall()]
    for uid in user_ids:
        has_env = conn.execute(
            "SELECT 1 FROM environments WHERE user_id = ? LIMIT 1", (uid,)
        ).fetchone()
        if not has_env:
            conn.execute(
                "INSERT INTO environments (user_id, name) VALUES (?, 'default')", (uid,)
            )

    # Backfill environment_id=0 rows to their user's default environment
    for tbl, user_col in [
        ("api_keys",       "user_id"),
        ("identities",     "user_id"),
        ("source_images",  "user_id"),
        ("jobs",           "user_id"),
    ]:
        conn.execute(f"""
            UPDATE {tbl} SET environment_id = (
                SELECT id FROM environments WHERE user_id = {tbl}.{user_col} AND name = 'default'
            ) WHERE environment_id = 0
        """)

    # detections and face_embeddings derive their environment from related rows
    conn.execute("""
        UPDATE detections SET environment_id = (
            SELECT environment_id FROM source_images WHERE source_images.id = detections.source_image_id
        ) WHERE environment_id = 0
    """)
    conn.execute("""
        UPDATE face_embeddings SET environment_id = (
            SELECT environment_id FROM identities WHERE identities.id = face_embeddings.identity_id
        ) WHERE environment_id = 0
    """)

    # Migration v1: recreate identities + source_images with environment_id in UNIQUE
    # constraint (ALTER TABLE cannot modify constraints, so must swap the table).
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_version < 1:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE identities_v2 (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                environment_id           INTEGER NOT NULL DEFAULT 0,
                type                     TEXT    NOT NULL CHECK(type IN ('face', 'object')),
                label                    TEXT    NOT NULL,
                cover_detection_id       INTEGER REFERENCES detections(id) ON DELETE SET NULL,
                representative_embedding BLOB,
                created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, environment_id, type, label)
            )
        """)
        conn.execute("""
            INSERT INTO identities_v2
                (id, user_id, environment_id, type, label,
                 cover_detection_id, representative_embedding, created_at)
            SELECT id, user_id, environment_id, type, label,
                   cover_detection_id, representative_embedding, created_at
            FROM identities
        """)
        conn.execute("DROP TABLE identities")
        conn.execute("ALTER TABLE identities_v2 RENAME TO identities")

        conn.execute("""
            CREATE TABLE source_images_v2 (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                environment_id INTEGER NOT NULL DEFAULT 0,
                file_path      TEXT    NOT NULL,
                width          INTEGER NOT NULL,
                height         INTEGER NOT NULL,
                uploaded_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, environment_id, file_path)
            )
        """)
        conn.execute("""
            INSERT INTO source_images_v2
                (id, user_id, environment_id, file_path, width, height, uploaded_at)
            SELECT id, user_id, environment_id, file_path, width, height, uploaded_at
            FROM source_images
        """)
        conn.execute("DROP TABLE source_images")
        conn.execute("ALTER TABLE source_images_v2 RENAME TO source_images")

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA user_version = 1")

    # Schema column additions
    existing_key_cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
    if "key_hint" not in existing_key_cols:
        conn.execute("ALTER TABLE api_keys ADD COLUMN key_hint TEXT NOT NULL DEFAULT ''")

    existing_model_cols = {r[1] for r in conn.execute("PRAGMA table_info(models)")}
    if "description" not in existing_model_cols:
        conn.execute("ALTER TABLE models ADD COLUMN description TEXT")
    if "config" not in existing_model_cols:
        conn.execute("ALTER TABLE models ADD COLUMN config TEXT")

    if "image_tags" not in {r[1] for r in conn.execute("PRAGMA table_info(source_images)")}:
        conn.execute("ALTER TABLE source_images ADD COLUMN image_tags TEXT")

    # external_ref: opaque caller-owned correlation id on identities + source_images
    if "external_ref" not in {r[1] for r in conn.execute("PRAGMA table_info(identities)")}:
        conn.execute("ALTER TABLE identities ADD COLUMN external_ref TEXT")
    if "external_ref" not in {r[1] for r in conn.execute("PRAGMA table_info(source_images)")}:
        conn.execute("ALTER TABLE source_images ADD COLUMN external_ref TEXT")

    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(detections)")}
    if "embedding" not in existing_cols:
        conn.execute("ALTER TABLE detections ADD COLUMN embedding BLOB")
    if "attributes" not in existing_cols:
        conn.execute("ALTER TABLE detections ADD COLUMN attributes TEXT")
    if "ignored" not in existing_cols:
        conn.execute("ALTER TABLE detections ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")

    existing_user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_approved" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 1")
    if "timezone" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
    if "locale" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT 'en-US'")
    if "last_environment_id" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_environment_id INTEGER")

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

    # Reconcile orphaned references left by older builds / direct DB edits.
    _reconcile_orphan_references(conn)

    # Remove identities that have zero detections (left by older builds).
    conn.execute(
        """DELETE FROM identities
           WHERE id NOT IN (
             SELECT DISTINCT identity_id FROM detections WHERE identity_id IS NOT NULL
           )"""
    )

    # Ensure FTS triggers store LOWER(label) for case-insensitive search.
    # If the existing trigger still inserts new.label (not LOWER), drop and recreate all three.
    try:
        ai_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='identities_fts_ai'"
        ).fetchone()
        if ai_sql is None or "LOWER" not in ai_sql[0]:
            for t in ("identities_fts_ai", "identities_fts_au", "identities_fts_ad"):
                conn.execute(f"DROP TRIGGER IF EXISTS {t}")
            conn.execute("""
                CREATE TRIGGER identities_fts_ai AFTER INSERT ON identities BEGIN
                    INSERT INTO identities_fts(rowid, label) VALUES (new.id, LOWER(new.label));
                END
            """)
            conn.execute("""
                CREATE TRIGGER identities_fts_au AFTER UPDATE OF label ON identities BEGIN
                    INSERT INTO identities_fts(identities_fts, rowid, label)
                        VALUES ('delete', old.id, LOWER(old.label));
                    INSERT INTO identities_fts(rowid, label) VALUES (new.id, LOWER(new.label));
                END
            """)
            conn.execute("""
                CREATE TRIGGER identities_fts_ad AFTER DELETE ON identities BEGIN
                    INSERT INTO identities_fts(identities_fts, rowid, label)
                        VALUES ('delete', old.id, LOWER(old.label));
                END
            """)
            # Repopulate with lowercased labels — can't use 'rebuild' here because the
            # content table would re-read mixed-case labels from identities.label.
            conn.execute("INSERT INTO identities_fts(identities_fts) VALUES ('delete-all')")
            conn.execute(
                "INSERT INTO identities_fts(rowid, label) SELECT id, LOWER(label) FROM identities"
            )
    except Exception:
        log.warning("FTS trigger migration failed — search may not be case-insensitive", exc_info=True)

    # Populate FTS index for new installations (identities exist but index is empty).
    try:
        identities_count = conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM identities_fts").fetchone()[0]
        if identities_count > 0 and fts_count == 0:
            conn.execute(
                "INSERT INTO identities_fts(rowid, label) SELECT id, LOWER(label) FROM identities"
            )
    except Exception:
        log.warning("FTS index population failed — search index may be empty", exc_info=True)


# ---------------------------------------------------------------------------
# Environment resolution helper
# ---------------------------------------------------------------------------

def _resolve_env(conn: sqlite3.Connection, user_id: int, environment_id: int | None) -> int:
    """Return a concrete environment_id for data scoping.

    When the caller passes an explicit environment_id, it is used as-is. When None
    (older single-environment call sites and tests), fall back to the user's default
    environment, creating it lazily if necessary so every user always has one.
    """
    if environment_id is not None:
        return int(environment_id)
    row = conn.execute(
        "SELECT id FROM environments WHERE user_id = ? AND name = 'default' LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return int(row[0])
    row = conn.execute(
        "SELECT id FROM environments WHERE user_id = ? ORDER BY id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return int(row[0])
    try:
        conn.execute(
            "INSERT INTO environments (user_id, name) VALUES (?, 'default')", (user_id,)
        )
    except sqlite3.IntegrityError:
        return 0  # user no longer exists; queries against env 0 return empty
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


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
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Every user always has a default environment so data scoping has a home.
        conn.execute(
            "INSERT OR IGNORE INTO environments (user_id, name) VALUES (?, 'default')",
            (user_id,),
        )
        return user_id


def save_last_environment(user_id: int, env_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET last_environment_id = ? WHERE id = ?",
            (env_id, user_id),
        )


def get_last_environment_id(user_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_environment_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row and row[0]:
            # Verify it still exists and belongs to this user
            env = conn.execute(
                "SELECT id FROM environments WHERE id = ? AND user_id = ?",
                (row[0], user_id),
            ).fetchone()
            return int(env[0]) if env else None
        return None


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


def list_managed_users(exclude_user_id: int) -> list[sqlite3.Row]:
    """All accounts except the given one (the admin viewing the page), for management."""
    with _connect() as conn:
        return conn.execute(
            """SELECT id, username, created_at, is_approved, is_admin
               FROM users WHERE id != ?
               ORDER BY is_approved ASC, created_at ASC""",
            (exclude_user_id,),
        ).fetchall()


def set_user_approved(user_id: int, approved: bool) -> bool:
    """Grant or revoke a non-admin account's access. Admin accounts are never changed."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET is_approved = ? WHERE id = ? AND is_admin = 0",
            (1 if approved else 0, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def delete_user(user_id: int) -> bool:
    """Delete a non-admin account and cascade all its data (identities, detections,
    references, source images, API keys). Admin accounts cannot be deleted."""
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
        return conn.execute("SELECT changes()").fetchone()[0] > 0


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

def create_api_key(
    user_id: int, key_hash: str, label: str,
    environment_id: int | None = None, key_hint: str = "",
) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            "INSERT INTO api_keys (user_id, environment_id, key_hash, label, key_hint) VALUES (?, ?, ?, ?, ?)",
            (user_id, env_id, key_hash, label, key_hint),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_api_key_user(key_hash: str) -> sqlite3.Row | None:
    """Return {key_id, user_id, environment_id, username} for an active key, or None."""
    with _connect() as conn:
        return conn.execute(
            """SELECT ak.id AS key_id, ak.user_id, ak.environment_id, u.username
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
            """SELECT ak.id, ak.label, ak.key_hint, ak.created_at, ak.last_used_at, ak.is_active,
                      ak.environment_id, e.name AS environment_name
               FROM api_keys ak
               LEFT JOIN environments e ON e.id = ak.environment_id
               WHERE ak.user_id = ? ORDER BY ak.created_at DESC""",
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


def rename_api_key(key_id: int, user_id: int, label: str) -> bool:
    with _connect() as conn:
        conn.execute(
            "UPDATE api_keys SET label = ? WHERE id = ? AND user_id = ?",
            (label, key_id, user_id),
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


def has_downloaded_model(model_type: str) -> bool:
    """Whether any model of this type has been downloaded (active or not)."""
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM models WHERE type = ? AND is_downloaded = 1 LIMIT 1",
            (model_type,),
        ).fetchone() is not None


def set_model_active(model_id: int, model_type: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE models SET is_active = 0 WHERE type = ?", (model_type,))
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (model_id,))


# ---------------------------------------------------------------------------
# Identities (per-user)
# ---------------------------------------------------------------------------

def search_identities(
    user_id: int, q: str, environment_id: int,
    limit: int = 10, identity_type: str | None = None,
) -> list[sqlite3.Row]:
    """FTS5 trigram search over identity labels; falls back to LIKE for <3-char queries."""
    _COLS = """
        SELECT i.id, i.label, i.type,
               COALESCE(d.crop_path, (
                   SELECT crop_path FROM detections
                   WHERE identity_id = i.id AND environment_id = i.environment_id
                     AND (review_status IS NULL OR review_status != 'rejected')
                   ORDER BY detected_at ASC, id ASC LIMIT 1
               )) AS cover_crop_path,
               (SELECT COUNT(DISTINCT dc.source_image_id) FROM detections dc WHERE dc.identity_id = i.id
                AND (dc.review_status IS NULL OR dc.review_status != 'rejected')) AS detection_count
        FROM identities i
        LEFT JOIN detections d ON d.id = i.cover_detection_id
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        type_filter = " AND i.type = ?" if identity_type else ""
        type_param = [identity_type] if identity_type else []
        q = q.strip().lower()

        prefix = f"{q}%"
        contains = f"%{q}%"

        if len(q) < 3:
            rows = conn.execute(
                _COLS + f"""
                WHERE i.user_id = ? AND i.environment_id = ?{type_filter}
                  AND LOWER(i.label) LIKE LOWER(?)
                ORDER BY CASE WHEN LOWER(i.label) LIKE LOWER(?) THEN 0 ELSE 1 END, i.label LIMIT ?
                """,
                [user_id, env_id] + type_param + [contains, prefix, limit],
            ).fetchall()
        else:
            safe_q = q.replace('"', '""')
            rows = []
            try:
                rows = conn.execute(
                    f"""
                    SELECT i.id, i.label, i.type,
                           COALESCE(d.crop_path, (
                               SELECT crop_path FROM detections
                               WHERE identity_id = i.id AND environment_id = i.environment_id
                                 AND (review_status IS NULL OR review_status != 'rejected')
                               ORDER BY detected_at ASC, id ASC LIMIT 1
                           )) AS cover_crop_path,
                           (SELECT COUNT(DISTINCT dc.source_image_id) FROM detections dc WHERE dc.identity_id = i.id
                            AND (dc.review_status IS NULL OR dc.review_status != 'rejected')) AS detection_count
                    FROM identities_fts
                    JOIN identities i ON identities_fts.rowid = i.id
                    LEFT JOIN detections d ON d.id = i.cover_detection_id
                    WHERE identities_fts MATCH ?
                      AND i.user_id = ? AND i.environment_id = ?{type_filter}
                    ORDER BY CASE WHEN LOWER(i.label) LIKE LOWER(?) THEN 0 ELSE 1 END,
                             bm25(identities_fts)
                    LIMIT ?
                    """,
                    [f'"{safe_q}"', user_id, env_id] + type_param + [prefix, limit],
                ).fetchall()
            except Exception:
                pass
            if not rows:
                rows = conn.execute(
                    _COLS + f"""
                    WHERE i.user_id = ? AND i.environment_id = ?{type_filter}
                      AND LOWER(i.label) LIKE LOWER(?)
                    ORDER BY CASE WHEN LOWER(i.label) LIKE LOWER(?) THEN 0 ELSE 1 END, i.label LIMIT ?
                    """,
                    [user_id, env_id] + type_param + [contains, prefix, limit],
                ).fetchall()
        return rows


def list_identities(
    user_id: int,
    identity_type: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    environment_id: int | None = None,
    external_ref: str | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = "SELECT * FROM identities WHERE user_id = ? AND environment_id = ?"
        params: list = [user_id, env_id]
        if identity_type:
            sql += " AND type = ?"
            params.append(identity_type)
        if external_ref:
            sql += " AND external_ref = ?"
            params.append(external_ref)
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


def record_change(
    conn: sqlite3.Connection, user_id: int, environment_id: int,
    entity_type: str, entity_id: int, action: str, external_ref: str | None = None,
) -> None:
    """Append one row to the change feed. Uses the caller's connection so the event
    commits in the same transaction as the mutation it describes."""
    conn.execute(
        """INSERT INTO changes (user_id, environment_id, entity_type, entity_id, action, external_ref)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, environment_id, entity_type, entity_id, action, external_ref),
    )


def create_identity(
    user_id: int, identity_type: str, label: str,
    environment_id: int | None = None, external_ref: str | None = None,
) -> int:
    try:
        with _connect() as conn:
            env_id = _resolve_env(conn, user_id, environment_id)
            conn.execute(
                "INSERT INTO identities (user_id, environment_id, type, label, external_ref) VALUES (?, ?, ?, ?, ?)",
                (user_id, env_id, identity_type, label, external_ref),
            )
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            record_change(conn, user_id, env_id, "identity", new_id, "created", external_ref)
            return new_id
    except sqlite3.IntegrityError as exc:
        raise DuplicateError(label) from exc


def rename_identity(
    identity_id: int, user_id: int, new_label: str, environment_id: int | None = None
) -> bool:
    try:
        with _connect() as conn:
            env_id = _resolve_env(conn, user_id, environment_id)
            # Purge first — if the identity has 0 detections it should not exist; skip the rename.
            purged = _purge_empty_identities(conn, user_id, env_id)
            if identity_id in purged:
                return True  # caller gets success; the identity is gone
            cur = conn.execute(
                "UPDATE identities SET label = ? WHERE id = ? AND user_id = ? AND environment_id = ?",
                (new_label, identity_id, user_id, env_id),
            )
            if cur.rowcount > 0:
                record_change(conn, user_id, env_id, "identity", identity_id, "relabeled")
            return cur.rowcount > 0
    except sqlite3.IntegrityError as exc:
        raise DuplicateError(new_label) from exc


def set_identity_external_ref(
    identity_id: int, user_id: int, external_ref: str | None, environment_id: int | None = None
) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        cur = conn.execute(
            "UPDATE identities SET external_ref = ? WHERE id = ? AND user_id = ? AND environment_id = ?",
            (external_ref, identity_id, user_id, env_id),
        )
        return cur.rowcount > 0


def delete_identity(
    identity_id: int, user_id: int, environment_id: int | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Delete an identity, its detections, and any source images that become orphaned.

    Returns (deleted, crop_filenames, source_filenames) so the caller can remove
    files from disk. face_embeddings are removed via FK ON DELETE CASCADE.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        if not conn.execute(
            "SELECT 1 FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (identity_id, user_id, env_id),
        ).fetchone():
            return False, [], []

        crops = [
            r["crop_path"]
            for r in conn.execute(
                "SELECT crop_path FROM detections WHERE identity_id = ? AND user_id = ?",
                (identity_id, user_id),
            ).fetchall()
            if r["crop_path"]
        ]

        source_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT source_image_id FROM detections WHERE identity_id = ? AND user_id = ?",
                (identity_id, user_id),
            ).fetchall()
        ]

        conn.execute(
            "DELETE FROM detections WHERE identity_id = ? AND user_id = ?",
            (identity_id, user_id),
        )

        sources = []
        for sid in source_ids:
            if conn.execute(
                "SELECT COUNT(*) FROM detections WHERE source_image_id = ?", (sid,)
            ).fetchone()[0] == 0:
                row = conn.execute(
                    "SELECT file_path FROM source_images WHERE id = ?", (sid,)
                ).fetchone()
                if row and row["file_path"]:
                    sources.append(row["file_path"])
                conn.execute("DELETE FROM source_images WHERE id = ?", (sid,))

        conn.execute(
            "DELETE FROM identities WHERE id = ? AND user_id = ?",
            (identity_id, user_id),
        )
        record_change(conn, user_id, env_id, "identity", identity_id, "deleted")
        return True, crops, sources


def delete_all_identities(user_id: int, environment_id: int | None = None) -> tuple[int, list[str]]:
    """Delete all identities and related data for a user in one environment.

    Returns (count, crop_filenames) so the caller can remove crop files from disk.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        count = conn.execute(
            "SELECT COUNT(*) FROM identities WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        ).fetchone()[0]
        crops = [
            r["crop_path"]
            for r in conn.execute(
                "SELECT crop_path FROM detections WHERE user_id = ? AND environment_id = ?",
                (user_id, env_id),
            ).fetchall()
            if r["crop_path"]
        ]
        sources = [
            r["file_path"]
            for r in conn.execute(
                "SELECT file_path FROM source_images WHERE user_id = ? AND environment_id = ?",
                (user_id, env_id),
            ).fetchall()
            if r["file_path"]
        ]
        conn.execute(
            "DELETE FROM face_embeddings WHERE environment_id = ? AND identity_id IN "
            "(SELECT id FROM identities WHERE user_id = ? AND environment_id = ?)",
            (env_id, user_id, env_id),
        )
        conn.execute(
            "DELETE FROM detections WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        )
        conn.execute(
            "DELETE FROM source_images WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        )
        conn.execute(
            "DELETE FROM identities WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        )
        return count, crops, sources


def count_identities(user_id: int, identity_type: str | None = None, environment_id: int | None = None) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql    = "SELECT COUNT(*) FROM identities WHERE user_id = ? AND environment_id = ?"
        params: list = [user_id, env_id]
        if identity_type:
            sql += " AND type = ?"
            params.append(identity_type)
        return conn.execute(sql, params).fetchone()[0]


def list_identities_summary(
    user_id: int,
    identity_type: str | None = None,
    cursor: str | None = None,
    limit: int = 30,
    environment_id: int | None = None,
) -> list[sqlite3.Row]:
    """Return identities with counts and thumbnail in a single query."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = """SELECT i.*,
                        COUNT(DISTINCT d.source_image_id) AS detection_count,
                        COUNT(DISTINCT fe.id)             AS embedding_count,
                        COALESCE(
                          (SELECT dc.crop_path FROM detections dc
                           WHERE dc.id = i.cover_detection_id),
                          (SELECT d2.crop_path FROM detections d2
                           WHERE d2.identity_id = i.id AND d2.user_id = i.user_id
                             AND d2.environment_id = i.environment_id
                             AND (d2.review_status IS NULL OR d2.review_status != 'rejected')
                           ORDER BY d2.detected_at ASC, d2.id ASC LIMIT 1)
                        ) AS thumbnail_crop
                 FROM identities i
                 LEFT JOIN detections d      ON d.identity_id = i.id
                                          AND (d.review_status IS NULL OR d.review_status != 'rejected')
                 LEFT JOIN face_embeddings fe ON fe.identity_id = i.id
                 WHERE i.user_id = ? AND i.environment_id = ?"""
        params: list = [user_id, env_id]
        if identity_type:
            sql += " AND i.type = ?"
            params.append(identity_type)
        if cursor:
            sql += " AND i.label > ?"
            params.append(cursor)
        sql += " GROUP BY i.id ORDER BY i.label LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def get_identity_with_counts(identity_id: int, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT i.*,
                      COUNT(DISTINCT d.source_image_id) AS detection_count,
                      COUNT(DISTINCT fe.id)             AS embedding_count,
                      COALESCE(
                        (SELECT d_cover.crop_path FROM detections d_cover
                         WHERE d_cover.id = i.cover_detection_id),
                        (SELECT d2.crop_path FROM detections d2
                         WHERE d2.identity_id = i.id AND d2.user_id = i.user_id
                           AND d2.environment_id = i.environment_id
                           AND (d2.review_status IS NULL OR d2.review_status != 'rejected')
                         ORDER BY d2.detected_at ASC, d2.id ASC LIMIT 1)
                      ) AS thumbnail_crop
               FROM identities i
               LEFT JOIN detections d  ON d.identity_id = i.id
                                      AND (d.review_status IS NULL OR d.review_status != 'rejected')
               LEFT JOIN face_embeddings fe ON fe.identity_id = i.id
               WHERE i.id = ? AND i.user_id = ? AND i.environment_id = ?
               GROUP BY i.id""",
            (identity_id, user_id, env_id),
        ).fetchone()


def set_identity_cover(identity_id: int, user_id: int, detection_id: int, environment_id: int | None = None) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            "UPDATE identities SET cover_detection_id = ? WHERE id = ? AND user_id = ? AND environment_id = ?",
            (detection_id, identity_id, user_id, env_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def get_identity_gallery(
    identity_id: int, user_id: int, cursor: str | None = None, limit: int = 30,
    environment_id: int | None = None, enrolled: bool | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        # LEFT JOIN face_embeddings (enrolled references are keyed by crop_path) so each
        # crop carries whether it's currently part of the reference set.
        sql = """SELECT d.id, d.crop_path, d.confidence, d.detected_at, d.review_status,
                        d.source_image_id, d.embedding, fe.id AS embedding_id,
                        si.file_path AS source_image_path
                 FROM detections d
                 LEFT JOIN face_embeddings fe
                        ON fe.identity_id = d.identity_id AND fe.source_image_path = d.crop_path
                 LEFT JOIN source_images si ON si.id = d.source_image_id
                 WHERE d.identity_id = ? AND d.user_id = ? AND d.environment_id = ?
                   AND (d.review_status IS NULL OR d.review_status != 'rejected')
                   AND NOT EXISTS (
                     SELECT 1 FROM detections d2
                     WHERE d2.identity_id = d.identity_id
                       AND d2.user_id = d.user_id
                       AND d2.environment_id = d.environment_id
                       AND d2.source_image_id = d.source_image_id
                       AND d2.id != d.id
                       AND (d2.confidence > d.confidence
                            OR (d2.confidence = d.confidence AND d2.id < d.id))
                   )"""
        params: list = [identity_id, user_id, env_id]
        if enrolled is True:
            sql += " AND fe.id IS NOT NULL"
        elif enrolled is False:
            sql += " AND fe.id IS NULL"
        if cursor:
            sql += " AND d.detected_at < ?"
            params.append(cursor)
        sql += " ORDER BY d.detected_at DESC, d.id DESC LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def get_unknown_face_embeddings(
    user_id: int, model_id: int, environment_id: int | None = None,
) -> list[sqlite3.Row]:
    """Unlabeled face detections (identity_id IS NULL) that carry an embedding for the
    given model. Used to cluster residual unknowns into suggested people."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT d.id, d.crop_path, d.confidence, d.embedding,
                      d.source_image_id, si.file_path AS source_image_path
               FROM detections d
               LEFT JOIN source_images si ON si.id = d.source_image_id
               WHERE d.user_id = ? AND d.environment_id = ? AND d.type = 'face'
                 AND d.identity_id IS NULL AND d.embedding IS NOT NULL AND d.model_id = ?
                 AND d.ignored = 0 AND (d.review_status IS NULL OR d.review_status != 'rejected')
               ORDER BY d.id""",
            (user_id, env_id, model_id),
        ).fetchall()


def dismiss_detections(user_id: int, detection_ids: list[int], environment_id: int | None = None) -> int:
    """Mark detections as ignored so they drop out of Suggested people, keeping the rows
    (still visible on the tag page / in the image's data). Returns how many were updated."""
    if not detection_ids:
        return 0
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        placeholders = ",".join("?" * len(detection_ids))
        cur = conn.execute(
            f"""UPDATE detections SET ignored = 1
                WHERE user_id = ? AND environment_id = ? AND id IN ({placeholders})""",
            (user_id, env_id, *detection_ids),
        )
        return cur.rowcount


def delete_detections(
    user_id: int, detection_ids: list[int], environment_id: int | None = None
) -> list[str]:
    """Permanently delete detections, returning their crop filenames so the caller can
    remove the files. Reconciles any orphaned references and records change events."""
    if not detection_ids:
        return []
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        placeholders = ",".join("?" * len(detection_ids))
        rows = conn.execute(
            f"""SELECT id, crop_path FROM detections
                WHERE user_id = ? AND environment_id = ? AND id IN ({placeholders})""",
            (user_id, env_id, *detection_ids),
        ).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        crops = [r["crop_path"] for r in rows]
        del_ph = ",".join("?" * len(ids))
        conn.execute(
            f"DELETE FROM detections WHERE user_id = ? AND environment_id = ? AND id IN ({del_ph})",
            (user_id, env_id, *ids),
        )
        for did in ids:
            record_change(conn, user_id, env_id, "detection", did, "deleted")
        _reconcile_orphan_references(conn, user_id)
        _purge_empty_identities(conn, user_id, env_id)
        return crops


def get_unknown_detections(
    user_id: int,
    detection_type: str | None = None,
    cursor: str | None = None,
    limit: int = 30,
    environment_id: int | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = """SELECT d.id, d.type, d.crop_path, d.confidence, d.detected_at,
                        d.source_image_id, si.file_path AS source_image_path
                 FROM detections d
                 LEFT JOIN source_images si ON si.id = d.source_image_id
                 WHERE d.user_id = ? AND d.environment_id = ? AND d.identity_id IS NULL
                   AND d.ignored = 0"""
        params: list = [user_id, env_id]
        if detection_type:
            sql += " AND d.type = ?"
            params.append(detection_type)
        if cursor:
            sql += " AND d.detected_at < ?"
            params.append(cursor)
        sql += " ORDER BY d.detected_at DESC, d.id DESC LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def insert_face_embedding(
    identity_id: int,
    model_id: int | None,
    embedding_bytes: bytes,
    source_image_path: str | None = None,
    environment_id: int | None = None,
) -> int:
    with _connect() as conn:
        # Inherit the environment from the owning identity when not given explicitly,
        # so face_embeddings rows are always scoped to the same environment.
        env_id = environment_id
        if env_id is None:
            row = conn.execute(
                "SELECT environment_id FROM identities WHERE id = ?", (identity_id,)
            ).fetchone()
            env_id = row[0] if row else 0
        conn.execute(
            """INSERT INTO face_embeddings (identity_id, environment_id, model_id, embedding, source_image_path)
               VALUES (?, ?, ?, ?, ?)""",
            (identity_id, env_id, model_id, embedding_bytes, source_image_path),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_or_create_identity(
    user_id: int, identity_type: str, label: str,
    environment_id: int | None = None, external_ref: str | None = None,
) -> tuple[int, bool]:
    """Return (identity_id, was_created). was_created is True only when a new row is inserted."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        # Case-insensitive lookup first — prevents duplicate identities when a caller
        # sends a different case than what's stored (e.g. "noah" vs "Noah").
        # Returns the oldest matching identity to stay deterministic if duplicates exist.
        existing = conn.execute(
            """SELECT id, external_ref FROM identities
               WHERE user_id = ? AND environment_id = ? AND type = ? AND LOWER(label) = LOWER(?)
               ORDER BY id ASC LIMIT 1""",
            (user_id, env_id, identity_type, label),
        ).fetchone()
        if existing:
            # Backfill external_ref if the caller now supplies one and it was unset —
            # captures the mapping at the first opportunity without overwriting.
            if external_ref and existing["external_ref"] is None:
                conn.execute(
                    "UPDATE identities SET external_ref = ? WHERE id = ?", (external_ref, existing["id"]),
                )
            return existing["id"], False
        try:
            conn.execute(
                "INSERT INTO identities (user_id, environment_id, type, label, external_ref) VALUES (?, ?, ?, ?, ?)",
                (user_id, env_id, identity_type, label, external_ref),
            )
        except sqlite3.IntegrityError:
            # Concurrent INSERT from another request beat us to the UNIQUE constraint.
            row = conn.execute(
                """SELECT id FROM identities
                   WHERE user_id = ? AND environment_id = ? AND type = ? AND LOWER(label) = LOWER(?)
                   ORDER BY id ASC LIMIT 1""",
                (user_id, env_id, identity_type, label),
            ).fetchone()
            return row["id"], False
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        record_change(conn, user_id, env_id, "identity", new_id, "created", external_ref)
        return new_id, True


def get_identity(identity_id: int, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (identity_id, user_id, env_id),
        ).fetchone()


# ---------------------------------------------------------------------------
# Source images (per-user)
# ---------------------------------------------------------------------------

def get_source_image(source_image_id: int, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM source_images WHERE id = ? AND user_id = ? AND environment_id = ?",
            (source_image_id, user_id, env_id),
        ).fetchone()


def list_source_images_by_ref(
    user_id: int, external_ref: str, environment_id: int | None = None
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM source_images WHERE user_id = ? AND environment_id = ? AND external_ref = ? ORDER BY id",
            (user_id, env_id, external_ref),
        ).fetchall()


def list_changes(
    user_id: int, since: int = 0, limit: int = 100, environment_id: int | None = None,
) -> list[sqlite3.Row]:
    """Change-feed rows with id > since, oldest first. id is the monotonic cursor."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT id, entity_type, entity_id, action, external_ref, created_at
               FROM changes
               WHERE user_id = ? AND environment_id = ? AND id > ?
               ORDER BY id ASC LIMIT ?""",
            (user_id, env_id, since, limit + 1),  # one extra to compute has_more
        ).fetchall()


def get_detections_by_ids(
    user_id: int, detection_ids: list[int], environment_id: int | None = None,
) -> list[sqlite3.Row]:
    """Fetch current state of multiple detections in one query (batch reconciliation)."""
    if not detection_ids:
        return []
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        placeholders = ",".join("?" * len(detection_ids))
        return conn.execute(
            f"""SELECT d.id, d.type, d.identity_id, d.confidence, d.review_status,
                       d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h, d.crop_path,
                       d.source_image_id, d.detected_at,
                       i.label AS identity_label, i.external_ref AS identity_external_ref,
                       si.external_ref AS source_external_ref
                FROM detections d
                LEFT JOIN identities i ON d.identity_id = i.id
                LEFT JOIN source_images si ON si.id = d.source_image_id
                WHERE d.user_id = ? AND d.environment_id = ? AND d.id IN ({placeholders})
                ORDER BY d.id""",
            (user_id, env_id, *detection_ids),
        ).fetchall()


def get_image_detections(
    source_image_id: int, user_id: int, det_type: str | None = None,
    environment_id: int | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = """SELECT d.id, d.type, d.identity_id, d.confidence, d.embedding,
                        d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                        d.crop_path, d.review_status, d.attributes,
                        i.label AS identity_label
                 FROM detections d
                 LEFT JOIN identities i ON d.identity_id = i.id
                 WHERE d.source_image_id = ? AND d.user_id = ? AND d.environment_id = ?"""
        params: list = [source_image_id, user_id, env_id]
        if det_type:
            sql += " AND d.type = ?"
            params.append(det_type)
        sql += " ORDER BY d.id"
        return conn.execute(sql, params).fetchall()


def clear_detections_for_source(
    source_image_id: int, user_id: int, det_type: str | None = None,
    environment_id: int | None = None,
) -> list[str]:
    """Delete detections for a source image (optionally just one type), keeping the
    source row. Returns removed crop filenames so the caller can delete the files.

    Used by detect's ``replace`` mode to make re-detecting the same image idempotent.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = "SELECT id, crop_path FROM detections WHERE source_image_id = ? AND user_id = ?"
        params: list = [source_image_id, user_id]
        if det_type:
            sql += " AND type = ?"
            params.append(det_type)
        dets = conn.execute(sql, params).fetchall()
        crops = [r["crop_path"] for r in dets]

        del_sql = "DELETE FROM detections WHERE source_image_id = ? AND user_id = ?"
        del_params: list = [source_image_id, user_id]
        if det_type:
            del_sql += " AND type = ?"
            del_params.append(det_type)
        conn.execute(del_sql, del_params)
        for d in dets:
            record_change(conn, user_id, env_id, "detection", d["id"], "deleted")

        # Drop any references whose crop was just removed, and recompute reps.
        _reconcile_orphan_references(conn, user_id)
        _purge_empty_identities(conn, user_id, env_id)
        return crops


def delete_source_image(source_image_id: int, user_id: int, environment_id: int | None = None) -> list[str] | None:
    """Delete a source image and cascade-delete all its detections (faces + objects).

    Returns the list of crop filenames that were removed (so the caller can delete
    the files on disk), or None if the source image was not found for this user.
    The content-hash source file itself is intentionally left on disk — it may be
    shared with other users/rows.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        row = conn.execute(
            "SELECT id FROM source_images WHERE id = ? AND user_id = ? AND environment_id = ?",
            (source_image_id, user_id, env_id),
        ).fetchone()
        if not row:
            return None
        dets = conn.execute(
            "SELECT id, crop_path FROM detections WHERE source_image_id = ? AND user_id = ?",
            (source_image_id, user_id),
        ).fetchall()
        crops = [r["crop_path"] for r in dets]
        # FK ON DELETE CASCADE removes the detections; SET NULL clears cover refs.
        conn.execute(
            "DELETE FROM source_images WHERE id = ? AND user_id = ?",
            (source_image_id, user_id),
        )
        for d in dets:
            record_change(conn, user_id, env_id, "detection", d["id"], "deleted")
        # Cascade removed the detections but not their references (keyed by crop_path,
        # not an FK) — reconcile so no orphaned references remain.
        _reconcile_orphan_references(conn, user_id)
        _purge_empty_identities(conn, user_id, env_id)
        return crops


def get_or_create_source_image(
    user_id: int, file_path: str, width: int, height: int,
    environment_id: int | None = None, external_ref: str | None = None,
) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            """INSERT OR IGNORE INTO source_images
               (user_id, environment_id, file_path, width, height, external_ref) VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, env_id, file_path, width, height, external_ref),
        )
        row = conn.execute(
            "SELECT id, external_ref FROM source_images WHERE user_id = ? AND environment_id = ? AND file_path = ?",
            (user_id, env_id, file_path),
        ).fetchone()
        # Backfill the ref if this content-hash row already existed without one.
        if external_ref and row["external_ref"] is None:
            conn.execute("UPDATE source_images SET external_ref = ? WHERE id = ?", (external_ref, row["id"]))
        return row["id"]


def get_all_source_file_paths() -> set[str]:
    """Return every file_path currently referenced in source_images (all users, all envs)."""
    with _connect() as conn:
        rows = conn.execute("SELECT file_path FROM source_images WHERE file_path IS NOT NULL").fetchall()
    return {r["file_path"] for r in rows}


def set_source_image_tags(source_image_id: int, tags_json: str) -> None:
    """Persist image-level keyword tags produced by a tagger engine (e.g. RAM++)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE source_images SET image_tags = ? WHERE id = ?",
            (tags_json, source_image_id),
        )


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
    attributes: str | None = None,
    environment_id: int | None = None,
) -> int:
    with _connect() as conn:
        # Inherit the environment from the source image when not given explicitly.
        env_id = environment_id
        src_ref = None
        src_row = conn.execute(
            "SELECT environment_id, external_ref FROM source_images WHERE id = ?", (source_image_id,)
        ).fetchone()
        if env_id is None:
            env_id = src_row[0] if src_row else _resolve_env(conn, user_id, None)
        if src_row is not None:
            src_ref = src_row["external_ref"]
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path, embedding, review_status, attributes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, env_id, identity_id, source_image_id, detection_type, model_id, confidence,
             bbox_x, bbox_y, bbox_w, bbox_h, crop_path, embedding, review_status, attributes),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        record_change(conn, user_id, env_id, "detection", new_id, "created", src_ref)
        return new_id


def get_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM detections WHERE id = ? AND user_id = ? AND environment_id = ?",
            (detection_id, user_id, env_id),
        ).fetchone()


def get_face_embedding(embedding_id: int, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    """Fetch a single face embedding row, verifying ownership via the parent identity."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT fe.id, fe.identity_id, fe.model_id, fe.source_image_path, fe.created_at
               FROM face_embeddings fe
               JOIN identities i ON i.id = fe.identity_id
               WHERE fe.id = ? AND i.user_id = ? AND i.environment_id = ?""",
            (embedding_id, user_id, env_id),
        ).fetchone()


def list_face_embeddings(identity_id: int) -> list[sqlite3.Row]:
    """List all reference embeddings for an identity (no raw vectors)."""
    with _connect() as conn:
        return conn.execute(
            """SELECT id, identity_id, model_id, source_image_path, created_at
               FROM face_embeddings WHERE identity_id = ? ORDER BY created_at""",
            (identity_id,),
        ).fetchall()


def embedding_exists(identity_id: int, source_image_path: str) -> bool:
    """Return True if a reference embedding for this crop already exists."""
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM face_embeddings WHERE identity_id = ? AND source_image_path = ?",
            (identity_id, source_image_path),
        ).fetchone() is not None


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


def cosine_similarity(emb: bytes | None, rep: bytes | None) -> float | None:
    """Cosine similarity between two raw float32 embedding blobs, rounded to 4 dp.
    Returns None if either is missing, zero-norm, or numpy is unavailable/stubbed.
    """
    if not emb or not rep:
        return None
    try:
        import numpy as np
        a = np.frombuffer(bytes(emb), dtype=np.float32)
        b = np.frombuffer(bytes(rep), dtype=np.float32)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return None
        return round(float(np.dot(a, b) / denom), 4)
    except Exception:
        return None


def best_cosine(emb: bytes | None, refs: list[bytes]) -> float | None:
    """Max cosine similarity between an embedding and any of the reference blobs.
    Used for the 'best match' display. Returns None if nothing comparable."""
    if not emb or not refs:
        return None
    try:
        import numpy as np
        a = np.frombuffer(bytes(emb), dtype=np.float32)
        na = float(np.linalg.norm(a))
        if na == 0:
            return None
        best = None
        for r in refs:
            b = np.frombuffer(bytes(r), dtype=np.float32)
            nb = float(np.linalg.norm(b))
            if nb == 0:
                continue
            s = float(np.dot(a, b) / (na * nb))
            if best is None or s > best:
                best = s
        return round(best, 4) if best is not None else None
    except Exception:
        return None


def get_reference_embeddings(model_id: int, user_id: int, environment_id: int | None = None) -> list[sqlite3.Row]:
    """All individual reference embeddings (identity_id, embedding) for a user/model.
    Used to build the 'best match' index (one vector per reference, not per identity).
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT fe.identity_id, fe.embedding
               FROM face_embeddings fe
               JOIN identities i ON i.id = fe.identity_id
               WHERE fe.model_id = ? AND i.user_id = ? AND i.environment_id = ?""",
            (model_id, user_id, env_id),
        ).fetchall()


def get_identity_reference_blobs(identity_id: int, user_id: int, environment_id: int | None = None) -> list[bytes]:
    """All reference embedding blobs for one identity (for max-over-references display)."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        rows = conn.execute(
            """SELECT fe.embedding
               FROM face_embeddings fe
               JOIN identities i ON i.id = fe.identity_id
               WHERE fe.identity_id = ? AND i.user_id = ? AND i.environment_id = ?
                 AND fe.embedding IS NOT NULL""",
            (identity_id, user_id, env_id),
        ).fetchall()
        return [bytes(r["embedding"]) for r in rows]


def get_oldest_detection_id(identity_id: int, user_id: int, environment_id: int | None = None) -> int | None:
    """Oldest non-rejected detection for an identity — stable default cover."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        row = conn.execute(
            """SELECT id FROM detections
               WHERE identity_id = ? AND user_id = ? AND environment_id = ?
                 AND (review_status IS NULL OR review_status != 'rejected')
               ORDER BY detected_at ASC, id ASC LIMIT 1""",
            (identity_id, user_id, env_id),
        ).fetchone()
        return row["id"] if row else None


def get_representative_embedding(identity_id: int, user_id: int, environment_id: int | None = None) -> bytes | None:
    """Return the stored representative embedding for an identity, or None."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        row = conn.execute(
            "SELECT representative_embedding FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (identity_id, user_id, env_id),
        ).fetchone()
        return bytes(row["representative_embedding"]) if row and row["representative_embedding"] else None


def _purge_empty_identities(conn, user_id: int, env_id: int) -> list[int]:
    """Delete identities that have no remaining detections in this environment.
    face_embeddings are removed via FK ON DELETE CASCADE. Returns the deleted ids."""
    empty = conn.execute(
        """SELECT id FROM identities
           WHERE user_id = ? AND environment_id = ?
             AND id NOT IN (
               SELECT DISTINCT identity_id FROM detections
               WHERE user_id = ? AND environment_id = ? AND identity_id IS NOT NULL
                 AND (review_status IS NULL OR review_status != 'rejected')
             )""",
        (user_id, env_id, user_id, env_id),
    ).fetchall()
    ids = [r["id"] for r in empty]
    if ids:
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM identities WHERE id IN ({ph})", ids)
        for iid in ids:
            record_change(conn, user_id, env_id, "identity", iid, "deleted")
    return ids


def _reconcile_orphan_references(conn, user_id: int | None = None) -> int:
    """Delete references whose source crop no longer has a detection, and null the
    representative of affected identities (recomputed lazily). Optionally scoped to
    one user. Returns the number of references removed. Operates on the given conn.
    """
    scope = "" if user_id is None else (
        " AND face_embeddings.identity_id IN (SELECT id FROM identities WHERE user_id = :uid)"
    )
    params = {} if user_id is None else {"uid": user_id}
    # Unaliased table name so the same predicate works in both SELECT and DELETE
    # (SQLite can't alias the DELETE target).
    orphan_where = f"""
        face_embeddings.source_image_path IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM detections d
            WHERE d.identity_id = face_embeddings.identity_id
              AND d.crop_path = face_embeddings.source_image_path){scope}"""

    affected = [r[0] for r in conn.execute(
        f"SELECT DISTINCT identity_id FROM face_embeddings WHERE {orphan_where}",
        params,
    ).fetchall()]
    if not affected:
        return 0

    conn.execute(f"DELETE FROM face_embeddings WHERE {orphan_where}", params)
    removed = conn.execute("SELECT changes()").fetchone()[0]
    conn.executemany(
        "UPDATE identities SET representative_embedding = NULL WHERE id = ?",
        [(i,) for i in affected],
    )
    return removed


def remove_reference_by_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    """Remove the reference embedding enrolled from this detection's crop and recompute
    the identity's representative. Returns True if a reference was removed.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        det = conn.execute(
            "SELECT identity_id, crop_path FROM detections WHERE id = ? AND user_id = ? AND environment_id = ?",
            (detection_id, user_id, env_id),
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


def get_representative_embeddings(model_id: int, user_id: int, environment_id: int | None = None) -> list[sqlite3.Row]:
    """Return identities with a representative embedding for this model."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT i.id AS identity_id, i.representative_embedding
               FROM identities i
               WHERE i.user_id = ? AND i.environment_id = ? AND i.representative_embedding IS NOT NULL
                 AND EXISTS (
                   SELECT 1 FROM face_embeddings fe
                   WHERE fe.identity_id = i.id AND fe.model_id = ?
                 )""",
            (user_id, env_id, model_id),
        ).fetchall()


def list_identity_ids_for_model(model_id: int, user_id: int, environment_id: int) -> list[int]:
    """Return distinct identity IDs that have face embeddings for this model/user/env."""
    with _connect() as conn:
        return [r[0] for r in conn.execute(
            """SELECT DISTINCT fe.identity_id FROM face_embeddings fe
               JOIN identities i ON i.id = fe.identity_id
               WHERE fe.model_id = ? AND i.user_id = ? AND i.environment_id = ?""",
            (model_id, user_id, environment_id),
        ).fetchall()]


def list_user_env_pairs_for_model(model_id: int) -> list[tuple[int, int]]:
    """Return all (user_id, environment_id) pairs that have face embeddings for this model."""
    with _connect() as conn:
        return [(r[0], r[1]) for r in conn.execute(
            """SELECT DISTINCT i.user_id, i.environment_id FROM identities i
               JOIN face_embeddings fe ON fe.identity_id = i.id
               WHERE fe.model_id = ?""",
            (model_id,),
        ).fetchall()]


def get_face_embeddings_for_model(model_id: int, user_id: int, environment_id: int | None = None) -> list[sqlite3.Row]:
    """Return embeddings for the active model scoped to this user's identities."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT fe.identity_id, fe.embedding, i.label
               FROM face_embeddings fe
               JOIN identities i ON fe.identity_id = i.id
               WHERE fe.model_id = ? AND i.user_id = ? AND i.environment_id = ?""",
            (model_id, user_id, env_id),
        ).fetchall()


def list_source_images(
    user_id: int,
    cursor: str | None = None,
    limit: int = 40,
    environment_id: int | None = None,
    identity_id: int | None = None,
    detection_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[sqlite3.Row]:
    """Processed source images, newest first, with per-image detection count.
    One row per image (deduped at ingestion by content hash). Cursor = 'uploadedAt_id'
    so ties on the second-precision uploaded_at don't drop or repeat rows across pages.
    Optional filters: identity_id (images containing that identity), detection_type,
    since/until (ISO timestamps, inclusive)."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        join = ""
        if identity_id is not None:
            join = """JOIN detections _id_f
                        ON _id_f.source_image_id = si.id AND _id_f.identity_id = ?"""
        sql = f"""SELECT si.id, si.file_path, si.width, si.height, si.uploaded_at, si.image_tags,
                         COUNT(DISTINCT d.id) AS detection_count
                  FROM source_images si
                  {join}
                  LEFT JOIN detections d ON d.source_image_id = si.id
                  WHERE si.user_id = ? AND si.environment_id = ?"""
        params: list = []
        if identity_id is not None:
            params.append(identity_id)
        params.extend([user_id, env_id])
        if detection_type:
            sql += " AND d.type = ?"
            params.append(detection_type)
        if since:
            sql += " AND si.uploaded_at >= ?"
            params.append(since)
        if until:
            sql += " AND si.uploaded_at <= ?"
            params.append(until)
        if cursor:
            try:
                c_ts, c_id = cursor.rsplit("_", 1)
                id_val = int(c_id)
            except ValueError:
                c_ts, id_val = cursor, 0
            sql += " AND (si.uploaded_at < ? OR (si.uploaded_at = ? AND si.id < ?))"
            params.extend([c_ts, c_ts, id_val])
        sql += " GROUP BY si.id ORDER BY si.uploaded_at DESC, si.id DESC LIMIT ?"
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


def count_source_images(user_id: int, environment_id: int | None = None) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT COUNT(*) FROM source_images WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        ).fetchone()[0]


def count_detections(user_id: int, environment_id: int | None = None) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT COUNT(*) FROM detections WHERE user_id = ? AND environment_id = ?",
            (user_id, env_id),
        ).fetchone()[0]


def count_unidentified(user_id: int, environment_id: int | None = None) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT COUNT(*) FROM detections
               WHERE user_id = ? AND environment_id = ?
                 AND identity_id IS NULL AND ignored = 0""",
            (user_id, env_id),
        ).fetchone()[0]


def count_pending_review(user_id: int, environment_id: int | None = None) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT COUNT(*) FROM detections
               WHERE user_id = ? AND environment_id = ?
                 AND review_status = 'pending' AND type = 'face'""",
            (user_id, env_id),
        ).fetchone()[0]


def get_review_queue(
    user_id: int,
    cursor: str | None = None,
    limit: int = 20,
    environment_id: int | None = None,
) -> list[sqlite3.Row]:
    """Pending face detections, lowest confidence first. Cursor = 'confidence_id'."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        dedup = """AND NOT EXISTS (
                     SELECT 1 FROM detections d2
                     WHERE d2.identity_id = d.identity_id
                       AND d2.user_id = d.user_id
                       AND d2.environment_id = d.environment_id
                       AND d2.source_image_id = d.source_image_id
                       AND d2.review_status = 'pending' AND d2.type = 'face'
                       AND d2.id != d.id
                       AND (d2.confidence < d.confidence
                            OR (d2.confidence = d.confidence AND d2.id < d.id))
                   )"""
        base_select = """SELECT d.id, d.source_image_id, d.model_id, d.confidence,
                          d.crop_path, d.detected_at, d.identity_id, d.embedding,
                          i.label AS current_label,
                          si.file_path AS source_image_path
                   FROM detections d
                   LEFT JOIN identities i ON d.identity_id = i.id
                   LEFT JOIN source_images si ON si.id = d.source_image_id
                   WHERE d.user_id = ? AND d.environment_id = ?
                     AND d.review_status = 'pending' AND d.type = 'face'"""
        if cursor:
            try:
                c_conf, c_id = cursor.rsplit("_", 1)
                conf_val = float(c_conf)
                id_val = int(c_id)
            except ValueError:
                conf_val, id_val = 0.0, 0
            rows = conn.execute(
                f"""{base_select}
                     {dedup}
                     AND (d.confidence > ? OR (d.confidence = ? AND d.id > ?))
                   ORDER BY d.confidence ASC, d.id ASC LIMIT ?""",
                (user_id, env_id, conf_val, conf_val, id_val, limit + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""{base_select}
                     {dedup}
                   ORDER BY d.confidence ASC, d.id ASC LIMIT ?""",
                (user_id, env_id, limit + 1),
            ).fetchall()
        return rows


def _detach_old_reference(conn, detection_id: int, user_id: int,
                          new_identity_id: int | None) -> int | None:
    """When a detection's identity is about to change or clear, drop the previous
    identity's reference for this crop — it no longer owns the crop, so keeping the
    embedding would leave an orphan that still pollutes that person's matching.
    Returns the old identity_id if a reference was removed (so the caller can recompute
    its representative), else None. Must be called BEFORE the identity UPDATE.
    """
    row = conn.execute(
        "SELECT identity_id, crop_path FROM detections WHERE id = ? AND user_id = ?",
        (detection_id, user_id),
    ).fetchone()
    if not row:
        return None
    old = row["identity_id"]
    if old is None or old == new_identity_id:
        return None
    conn.execute(
        "DELETE FROM face_embeddings WHERE identity_id = ? AND source_image_path = ?",
        (old, row["crop_path"]),
    )
    return old if conn.execute("SELECT changes()").fetchone()[0] > 0 else None


def _recompute_representative(identity_id: int | None) -> None:
    if identity_id is None:
        return
    model_row = get_active_model("face")
    if model_row:
        compute_and_store_representative(identity_id, model_row["id"])


def confirm_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            """UPDATE detections SET review_status = 'confirmed', reviewed_at = datetime('now')
               WHERE id = ? AND user_id = ? AND environment_id = ?""",
            (detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            # Auto-confirm any pending siblings from the same source image for the same
            # identity so they don't accumulate in the review queue or gallery.
            conn.execute(
                """UPDATE detections SET review_status = 'confirmed', reviewed_at = datetime('now')
                   WHERE user_id = ? AND environment_id = ? AND review_status = 'pending'
                     AND type = 'face' AND id != ?
                     AND source_image_id = (SELECT source_image_id FROM detections WHERE id = ?)
                     AND identity_id    = (SELECT identity_id    FROM detections WHERE id = ?)""",
                (user_id, env_id, detection_id, detection_id, detection_id),
            )
        return changed


def unidentify_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    """Clear a detection's identity and return it to the unidentified queue.
    Distinct from reject: reject keeps identity_id (wrong match, stays under that person);
    unidentify clears it so the face can be re-labeled as someone else."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        old_id = _detach_old_reference(conn, detection_id, user_id, None)
        conn.execute(
            """UPDATE detections SET identity_id = NULL, review_status = NULL,
               reviewed_at = NULL WHERE id = ? AND user_id = ? AND environment_id = ?""",
            (detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
            _purge_empty_identities(conn, user_id, env_id)
    _recompute_representative(old_id)
    return changed


def reject_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        # Detach the face reference so it doesn't pollute matching, but keep identity_id so
        # the detection remains findable under this person and can be restored later.
        old_id = _detach_old_reference(conn, detection_id, user_id, None)
        conn.execute(
            """UPDATE detections SET review_status = 'rejected',
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ? AND environment_id = ?""",
            (detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
            _purge_empty_identities(conn, user_id, env_id)
    _recompute_representative(old_id)
    return changed


def restore_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    """Un-reject: mark confirmed, keep identity_id. Caller re-enrolls the face embedding."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            """UPDATE detections SET review_status = 'confirmed', reviewed_at = datetime('now')
               WHERE id = ? AND user_id = ? AND environment_id = ? AND review_status = 'rejected'""",
            (detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
    return changed


def get_rejected_detections(
    identity_id: int, user_id: int, environment_id: int | None = None,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            """SELECT d.id, d.crop_path, d.detected_at, d.source_image_id,
                      si.file_path AS source_image_path
               FROM detections d
               LEFT JOIN source_images si ON si.id = d.source_image_id
               WHERE d.identity_id = ? AND d.user_id = ? AND d.environment_id = ?
                 AND d.review_status = 'rejected'
               ORDER BY d.detected_at DESC, d.id DESC""",
            (identity_id, user_id, env_id),
        ).fetchall()


def reassign_detection(detection_id: int, user_id: int, identity_id: int, environment_id: int | None = None) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        old_id = _detach_old_reference(conn, detection_id, user_id, identity_id)
        conn.execute(
            """UPDATE detections SET review_status = 'reassigned', identity_id = ?,
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ? AND environment_id = ?""",
            (identity_id, detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:  # identity changed — surface as a relabel for delta-sync clients
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
            _purge_empty_identities(conn, user_id, env_id)
    _recompute_representative(old_id)
    return changed


def delete_detection(detection_id: int, user_id: int, environment_id: int | None = None) -> bool:
    """Delete a detection. Also removes any reference embedding enrolled from its crop
    (keeping the reference count consistent) and recomputes the representative. The
    cover photo is cleared automatically via the cover_detection_id ON DELETE SET NULL FK.
    """
    ref_identity = None
    purged_ids: list[int] = []
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        row = conn.execute(
            "SELECT identity_id, crop_path FROM detections WHERE id = ? AND user_id = ? AND environment_id = ?",
            (detection_id, user_id, env_id),
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
            "DELETE FROM detections WHERE id = ? AND user_id = ? AND environment_id = ?",
            (detection_id, user_id, env_id),
        )
        deleted = conn.execute("SELECT changes()").fetchone()[0] > 0
        if deleted:
            record_change(conn, user_id, env_id, "detection", detection_id, "deleted")
            purged_ids = _purge_empty_identities(conn, user_id, env_id)

    if ref_identity is not None and ref_identity not in purged_ids:
        model_row = get_active_model("face")
        if model_row:
            compute_and_store_representative(ref_identity, model_row["id"])
    return deleted


def get_identity_source_pairs(user_id: int, environment_id: int | None = None) -> set[tuple[int, int]]:
    """Return all (source_image_id, identity_id) pairs that already have a detection,
    used by scan_unidentified to avoid linking a duplicate crop from the same image."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        rows = conn.execute(
            """SELECT source_image_id, identity_id FROM detections
               WHERE user_id = ? AND environment_id = ? AND identity_id IS NOT NULL
                 AND type = 'face'""",
            (user_id, env_id),
        ).fetchall()
        return {(int(r[0]), int(r[1])) for r in rows}


def suggest_detection(detection_id: int, user_id: int, identity_id: int, environment_id: int | None = None) -> bool:
    """Auto-match path: set a tentative identity (review_status stays 'pending') so the
    detection appears in the review queue without being auto-confirmed into the gallery."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            """UPDATE detections SET identity_id = ?
               WHERE id = ? AND user_id = ? AND environment_id = ? AND identity_id IS NULL""",
            (identity_id, detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
        return changed


def label_detection(detection_id: int, user_id: int, identity_id: int, environment_id: int | None = None) -> bool:
    """Casual correction: set identity and mark confirmed. Drops the previous
    identity's reference for this crop so it doesn't orphan when the label changes."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        old_id = _detach_old_reference(conn, detection_id, user_id, identity_id)
        conn.execute(
            """UPDATE detections SET identity_id = ?, review_status = 'confirmed',
               reviewed_at = datetime('now') WHERE id = ? AND user_id = ? AND environment_id = ?""",
            (identity_id, detection_id, user_id, env_id),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        if changed:
            record_change(conn, user_id, env_id, "detection", detection_id, "relabeled")
            _purge_empty_identities(conn, user_id, env_id)
    _recompute_representative(old_id)
    return changed


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

# (type, name, embedding_dim, description). All object models produce bounding boxes.
_MODEL_SEED: list[tuple] = [
    # (type, name, embedding_dim, description, config)
    # config is a JSON string for compound models; None for all single-file models.

    # --- Face (InsightFace packs: detection + ArcFace embeddings + age/gender/pose) ---
    ("face",   "buffalo_l",       512,  "Large pack (RetinaFace + ArcFace). Best accuracy. Recommended default.", None),
    ("face",   "buffalo_s",       512,  "Small pack — faster, lighter, slightly lower accuracy than buffalo_l.", None),
    ("face",   "buffalo_sc",      512,  "Compact pack — detect + recognize only (no age/gender/pose). Lightest.", None),
    ("face",   "antelopev2",      512,  "ResNet100/glint360k pack. Accuracy comparable to buffalo_l.", None),

    # --- Object: standard YOLO (fixed 80 COCO classes) ---
    ("object", "yolov8n",         None, "YOLOv8 nano — fastest, lowest accuracy. 80 COCO classes.", None),
    ("object", "yolov8s",         None, "YOLOv8 small — good speed/accuracy balance. 80 COCO classes.", None),
    ("object", "yolov8m",         None, "YOLOv8 medium — more accurate, slower. 80 COCO classes.", None),
    ("object", "yolov8l",         None, "YOLOv8 large — higher accuracy, heavier. 80 COCO classes.", None),
    ("object", "yolov8x",         None, "YOLOv8 extra-large — most accurate v8, slowest. 80 COCO classes.", None),

    # --- Object: YOLO11 (newer generation; better accuracy per size) ---
    ("object", "yolo11n",         None, "YOLO11 nano — newer gen; beats yolov8n at similar speed.", None),
    ("object", "yolo11s",         None, "YOLO11 small — newer gen; strong speed/accuracy balance.", None),
    ("object", "yolo11m",         None, "YOLO11 medium — newer gen; more accurate, slower.", None),
    ("object", "yolo11l",         None, "YOLO11 large — newer gen; high accuracy.", None),
    ("object", "yolo11x",         None, "YOLO11 extra-large — newer gen; best YOLO11 accuracy, slowest.", None),

    # --- Object: YOLOv10 (NMS-free, efficient) ---
    ("object", "yolov10s",        None, "YOLOv10 small — NMS-free, efficient. 80 COCO classes.", None),
    ("object", "yolov10m",        None, "YOLOv10 medium — NMS-free; accuracy/speed balance.", None),
    ("object", "yolov10l",        None, "YOLOv10 large — NMS-free; higher accuracy.", None),
    ("object", "yolov10x",        None, "YOLOv10 extra-large — NMS-free; top YOLOv10 accuracy.", None),

    # --- Object: RT-DETR (transformer detector; strong accuracy, real-time) ---
    ("object", "rtdetr-l",        None, "RT-DETR large — transformer detector; strong accuracy, real-time.", None),
    ("object", "rtdetr-x",        None, "RT-DETR extra-large — transformer detector; highest accuracy, heavier.", None),

    # --- Object: YOLO-World (open vocabulary — detect anything you describe) ---
    ("object", "yolov8s-worldv2", None, "YOLO-World small — open vocabulary; detect any terms you define.", None),
    ("object", "yolov8m-worldv2", None, "YOLO-World medium — open vocabulary; better accuracy than small.", None),
    ("object", "yolov8l-worldv2", None, "YOLO-World large — open vocabulary; best open-vocab accuracy, slower.", None),

    # --- Object: Florence-2 (grounded open-vocabulary detector; describes what it finds) ---
    ("object", "florence-2-base", None,
     "Florence-2 base — grounded open-vocabulary detection; free-form labels, no class list. "
     "Heavier than YOLO. Recommended default.", None),

    # --- Object: RAM++ + Grounding DINO (keyword tagger + open-vocabulary localizer) ---
    # Compound model: RAM++ generates image-level keyword tags; Grounding DINO localizes each
    # tag with a bounding box. Both components download and activate as a single unit.
    ("object", "ram-plus-plus-grounding-dino", None,
     "RAM++ + Grounding DINO — generates descriptive keyword tags for the whole image and "
     "localizes each tag with a bounding box. Open vocabulary, no fixed class list. "
     "More descriptive than YOLO or Florence.",
     '{"tagger":"ram-plus-plus","detector":"grounding-dino-base"}'),
]

# Default vocabulary for YOLO-World: 80 COCO classes + common extras
# Broad common-object vocabulary for general photo tagging, favouring coverage over
# peak precision: a wide spread of everyday people, animals, vehicles, food,
# household, tools and outdoor items. Still omits security/military, medical and
# abstract-event terms ("weapon", "accident"), which lack crisp visual anchors.
# A vocabulary this size raises false positives and the one-time text-encode cost
# on a vocab change; per-image latency stays dominated by the backbone. Trim it on
# the Settings page if precision matters more than breadth for a given deployment.
_WORLD_CLASSES_DEFAULT = (
    # People
    "person,"
    # Animals - mammals
    "dog,cat,rabbit,hamster,guinea pig,horse,cow,sheep,goat,pig,donkey,llama,"
    "alpaca,camel,deer,fox,wolf,bear,panda,raccoon,squirrel,hedgehog,bat,koala,"
    "kangaroo,monkey,gorilla,chimpanzee,elephant,lion,tiger,leopard,cheetah,"
    "giraffe,zebra,rhinoceros,hippopotamus,buffalo,seal,otter,dolphin,whale,"
    # Animals - birds
    "bird,chicken,rooster,duck,goose,turkey,swan,pigeon,seagull,owl,eagle,hawk,"
    "parrot,peacock,crow,penguin,flamingo,"
    # Animals - reptiles, fish, marine
    "fish,turtle,frog,snake,lizard,gecko,crocodile,shark,octopus,crab,lobster,"
    "starfish,jellyfish,seahorse,"
    # Animals - insects
    "butterfly,bee,ant,ladybug,dragonfly,spider,mosquito,snail,caterpillar,"
    # Vehicles
    "car,truck,pickup truck,suv,van,taxi,bus,motorcycle,bicycle,scooter,tricycle,"
    "segway,boat,sailboat,kayak,canoe,ship,ferry,jet ski,airplane,helicopter,"
    "hot air balloon,drone,train,tram,ambulance,fire truck,police car,"
    "garbage truck,tractor,forklift,crane,excavator,trailer,golf cart,snowmobile,"
    "wheelchair,shopping cart,wagon,"
    # Food - fruit
    "apple,banana,orange,strawberry,grape,blueberry,raspberry,watermelon,melon,"
    "lemon,lime,pineapple,peach,pear,plum,cherry,kiwi,coconut,avocado,fig,"
    # Food - vegetables
    "tomato,potato,carrot,corn,broccoli,cauliflower,cabbage,lettuce,spinach,celery,"
    "cucumber,onion,garlic,bell pepper,pumpkin,zucchini,eggplant,peas,green beans,"
    "mushroom,"
    # Food - prepared
    "bread,baguette,croissant,bagel,muffin,sandwich,hamburger,pizza,taco,burrito,"
    "sushi,hot dog,french fries,pretzel,chips,popcorn,pasta,noodles,rice,soup,"
    "salad,steak,sausage,bacon,ham,egg,cheese,butter,yogurt,cake,cupcake,"
    "birthday cake,donut,cookie,pie,brownie,ice cream,chocolate,candy,pancake,"
    "waffle,"
    # Drinks and tableware
    "coffee cup,mug,teapot,kettle,water bottle,wine glass,wine bottle,beer bottle,"
    "soda can,milk carton,bottle,cup,jug,thermos,plate,bowl,fork,knife,spoon,"
    "chopsticks,straw,"
    # Kitchen
    "pot,pan,frying pan,blender,mixer,coffee maker,cutting board,colander,whisk,"
    "spatula,ladle,tongs,grater,can opener,rolling pin,measuring cup,jar,can,"
    "napkin,paper towel,sponge,dish rack,apron,oven mitt,salt shaker,pepper shaker,"
    "lunchbox,dishwasher,"
    # Nature
    "tree,flower,rose,sunflower,tulip,potted plant,cactus,palm tree,grass,leaf,"
    "bush,mountain,hill,cliff,cave,beach,ocean,wave,lake,river,waterfall,pond,rock,"
    "sand,snow,ice,cloud,rainbow,"
    # Buildings and places
    "house,building,skyscraper,barn,shed,garage,greenhouse,church,castle,tower,"
    "lighthouse,windmill,silo,water tower,bridge,tunnel,gazebo,pier,fountain,"
    "statue,monument,store,restaurant,hotel,door,window,roof,chimney,balcony,"
    "stairs,escalator,elevator,fence,gate,mailbox,bench,picnic table,"
    # Infrastructure and traffic
    "street light,traffic light,stop sign,street sign,traffic cone,fire hydrant,"
    "parking meter,telephone pole,power line,bus stop,billboard,"
    # Furniture
    "armchair,ottoman,coffee table,dining table,desk,stool,bed,bunk bed,crib,"
    "mattress,dresser,wardrobe,cabinet,cupboard,shelf,bookshelf,drawer,nightstand,"
    "coat rack,hanger,chair,couch,table,"
    # Home objects
    "lamp,floor lamp,desk lamp,chandelier,light bulb,flashlight,lampshade,mirror,"
    "clock,wall clock,alarm clock,picture frame,painting,poster,vase,candle,"
    "candle holder,rug,pillow,cushion,blanket,comforter,curtain,blinds,fireplace,"
    "fan,heater,radiator,air conditioner,humidifier,thermostat,smoke detector,"
    "trash can,recycling bin,laundry basket,basket,bucket,box,crate,barrel,doormat,"
    "key,keychain,lock,padlock,trophy,"
    # Appliances and bathroom
    "refrigerator,oven,microwave,toaster,stove,sink,toilet,bathtub,shower,"
    "shower head,faucet,toilet paper,toilet brush,plunger,soap,soap dispenser,"
    "shampoo,toothbrush,toothpaste,razor,comb,hairbrush,hair dryer,tissue,"
    "tissue box,towel,bath mat,shower curtain,lipstick,perfume,nail polish,scale,"
    # Electronics
    "tv,laptop,computer,monitor,tablet,telephone,cell phone,smartwatch,keyboard,"
    "mouse,camera,webcam,video camera,tripod,headphones,earbuds,speaker,microphone,"
    "projector,printer,scanner,router,modem,flash drive,battery,charger,cable,"
    "power strip,remote,game console,game controller,vr headset,radio,calculator,"
    # Office and stationery
    "book,newspaper,magazine,pen,pencil,marker,highlighter,crayon,paintbrush,"
    "eraser,ruler,scissors,stapler,tape,glue,sticky note,notebook,folder,binder,"
    "clipboard,envelope,paper,push pin,sticker,briefcase,whiteboard,"
    # Tools and yard
    "hammer,screwdriver,wrench,pliers,drill,saw,chainsaw,axe,chisel,level,"
    "tape measure,utility knife,crowbar,clamp,screw,nail,bolt,toolbox,workbench,"
    "ladder,rope,chain,wire,duct tape,hose,shovel,spade,rake,trowel,lawn mower,"
    "leaf blower,wheelbarrow,watering can,flower pot,sprinkler,grill,fire pit,"
    "bird feeder,"
    # Clothing and accessories
    "backpack,handbag,suitcase,duffel bag,tote bag,wallet,purse,umbrella,glasses,"
    "sunglasses,watch,hat,cap,helmet,scarf,tie,shirt,t-shirt,sweater,hoodie,dress,"
    "skirt,shorts,jacket,coat,blazer,vest,suit,jeans,pajamas,swimsuit,sock,glove,"
    "belt,shoe,sneaker,boot,sandal,high heels,necklace,ring,bracelet,earring,"
    # Sports and recreation
    "soccer ball,basketball,football,baseball,volleyball,golf ball,tennis racket,"
    "baseball bat,hockey stick,bowling ball,skateboard,surfboard,skis,snowboard,"
    "kite,frisbee,fishing rod,golf club,dumbbell,barbell,kettlebell,yoga mat,"
    "jump rope,treadmill,roller skates,ice skates,paddle,life jacket,tent,"
    "sleeping bag,cooler,lantern,hammock,"
    # Musical instruments
    "guitar,piano,violin,cello,drum,trumpet,trombone,saxophone,clarinet,flute,"
    "harmonica,accordion,harp,banjo,ukulele,xylophone,tambourine,"
    # Toys, baby, decor
    "teddy bear,doll,stuffed animal,toy,toy car,robot,building blocks,puzzle,"
    "balloon,baby stroller,baby bottle,pacifier,high chair,car seat,christmas tree,"
    "wreath,fireworks,sparkler,flag,banner,gift box,ribbon"
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
    ("face.match_strategy",
     "best", "string", "face",
     "Face Matching Method | How a face is scored against each saved person. "
     "Best match compares against each reference photo and uses the closest — better when "
     "someone looks different across photos (age, glasses, lighting), and keeps scores intuitive. "
     "Average blends all of a person's reference photos into one — faster and steadier, but "
     "individual photo scores drift as you add varied references."),
    ("face.detection_confidence",
     "0.6",   "float",  "face",
     "Detection Confidence | Minimum confidence for a face region to be reported at all"),
    ("face.min_face_size",
     "40",    "int",    "face",
     "Minimum Face Size | Faces smaller than this many pixels wide or tall are ignored"),
    ("face.cluster_threshold",
     "0.5",   "float",  "face",
     "Suggested-People Threshold | How similar two unknown faces must be (0–1) to be grouped "
     "as the same person on the Suggested People page. Higher splits more; lower merges more"),
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
    ("system.compress_on_ingest",
     "true",  "bool",   "system",
     "Compress on Ingest | Re-encode source images as JPEG on save to reduce storage. "
     "Loses original quality; crops are unaffected."),
    ("system.ingest_jpeg_quality",
     "85",    "int",    "system",
     "Ingest JPEG Quality | JPEG quality (1–95) used when Compress on Ingest is enabled"),
    ("system.use_gpu",
     "true",  "bool",   "system",
     "Use GPU | Enable GPU inference when a CUDA device is available; disable to force CPU"),
    ("system.auto_approve_users",
     "true",  "bool",   "system",
     "Auto-approve New Users | Approve new accounts immediately on sign-up; disable to require admin approval"),
    ("system.log_buffer_size",
     "500",   "int",    "system",
     "Log Buffer Size | Number of recent log lines kept in memory and replayed in the log viewer (100–100000)"),
]


def get_settings_defaults() -> dict[str, str]:
    """Return {key: default_value} from seed data — used by the reset endpoint."""
    return {row[0]: row[1] for row in _SETTINGS_SEED}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(user_id: int, job_type: str, environment_id: int | None = None) -> str:
    job_id = uuid.uuid4().hex
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            "INSERT INTO jobs (id, user_id, environment_id, type) VALUES (?, ?, ?, ?)",
            (job_id, user_id, env_id, job_type),
        )
    return job_id


def update_job(job_id: str, status: str, result: object = None) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE jobs SET status = ?, result = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (status, json.dumps(result) if result is not None else None, job_id),
        )


def get_job(job_id: str, user_id: int, environment_id: int | None = None) -> sqlite3.Row | None:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND user_id = ? AND environment_id = ?",
            (job_id, user_id, env_id),
        ).fetchone()


def list_jobs(user_id: int, limit: int = 50, environment_id: int | None = None) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        return conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND environment_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, env_id, limit),
        ).fetchall()


def delete_job(job_id: str, user_id: int, environment_id: int | None = None) -> bool:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        cur = conn.execute(
            "DELETE FROM jobs WHERE id = ? AND user_id = ? AND environment_id = ?",
            (job_id, user_id, env_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------

def create_environment(user_id: int, name: str) -> int:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO environments (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError as exc:
        raise DuplicateError(name) from exc


def list_environments(user_id: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM environments WHERE user_id = ? ORDER BY name ASC",
            (user_id,),
        ).fetchall()


def get_environment(env_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM environments WHERE id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchone()


def get_default_environment_id(user_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM environments WHERE user_id = ? AND name = 'default' LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return row[0]
        # Fall back to first environment
        row = conn.execute(
            "SELECT id FROM environments WHERE user_id = ? ORDER BY id ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row[0] if row else None


def rename_environment(env_id: int, user_id: int, name: str) -> bool:
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE environments SET name = ? WHERE id = ? AND user_id = ?",
                (name, env_id, user_id),
            )
            return cur.rowcount > 0
    except sqlite3.IntegrityError as exc:
        raise DuplicateError(name) from exc


def delete_environment(env_id: int, user_id: int) -> tuple[bool, list[str], list[str]]:
    """Delete environment and all its data. Returns (deleted, crop_paths, source_paths)."""
    with _connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM environments WHERE id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchone():
            return False, [], []
        crops = [r["crop_path"] for r in conn.execute(
            "SELECT crop_path FROM detections WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchall() if r["crop_path"]]
        sources = [r["file_path"] for r in conn.execute(
            "SELECT file_path FROM source_images WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchall() if r["file_path"]]
        conn.execute(
            "DELETE FROM face_embeddings WHERE environment_id = ?", (env_id,)
        )
        conn.execute(
            "DELETE FROM detections WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        )
        conn.execute(
            "DELETE FROM source_images WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        )
        conn.execute(
            "DELETE FROM identities WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        )
        conn.execute(
            "DELETE FROM jobs WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        )
        conn.execute(
            "DELETE FROM environments WHERE id = ? AND user_id = ?",
            (env_id, user_id),
        )
        return True, crops, sources


def get_environment_stats(env_id: int, user_id: int) -> dict:
    with _connect() as conn:
        identities = conn.execute(
            "SELECT COUNT(*) FROM identities WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchone()[0]
        detections = conn.execute(
            "SELECT COUNT(*) FROM detections WHERE environment_id = ? AND user_id = ?",
            (env_id, user_id),
        ).fetchone()[0]
        return {"identities": identities, "detections": detections}


# ---------------------------------------------------------------------------
# Identity merge
# ---------------------------------------------------------------------------

def merge_identities(
    source_id: int, target_id: int, user_id: int, environment_id: int | None = None,
) -> bool:
    """Reassign all detections and embeddings from source to target identity, then delete
    source. Returns False if either identity doesn't belong to this user/env."""
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        src = conn.execute(
            "SELECT id FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (source_id, user_id, env_id),
        ).fetchone()
        tgt = conn.execute(
            "SELECT id FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (target_id, user_id, env_id),
        ).fetchone()
        if not src or not tgt:
            return False
        conn.execute(
            """UPDATE detections SET identity_id = ?
               WHERE identity_id = ? AND user_id = ? AND environment_id = ?""",
            (target_id, source_id, user_id, env_id),
        )
        conn.execute(
            "UPDATE face_embeddings SET identity_id = ? WHERE identity_id = ?",
            (target_id, source_id),
        )
        conn.execute(
            "DELETE FROM identities WHERE id = ? AND user_id = ? AND environment_id = ?",
            (source_id, user_id, env_id),
        )
        record_change(conn, user_id, env_id, "identity", target_id, "relabeled")
        record_change(conn, user_id, env_id, "identity", source_id, "deleted")
        return True


# ---------------------------------------------------------------------------
# Detection search (cross-entity)
# ---------------------------------------------------------------------------

def search_source_images(
    user_id: int,
    environment_id: int | None = None,
    identity_ids: list[int] | None = None,
    detection_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    confidence_min: float | None = None,
    cursor: str | None = None,
    limit: int = 40,
) -> list[sqlite3.Row]:
    """Return source images matching all supplied filters.

    identity_ids uses AND semantics — every listed identity must appear in the image.
    Other filters (type, since, until, confidence_min) apply to detections in the image.
    """
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        params: list = [user_id, env_id]
        extra_where: list[str] = []

        if identity_ids:
            placeholders = ",".join("?" * len(identity_ids))
            extra_where.append(f"""si.id IN (
                SELECT source_image_id FROM detections
                WHERE user_id = ? AND environment_id = ? AND identity_id IN ({placeholders})
                GROUP BY source_image_id
                HAVING COUNT(DISTINCT identity_id) = ?
            )""")
            params.extend([user_id, env_id, *identity_ids, len(identity_ids)])

        if detection_type:
            extra_where.append("""EXISTS (
                SELECT 1 FROM detections _dt
                WHERE _dt.source_image_id = si.id AND _dt.type = ?
            )""")
            params.append(detection_type)

        if confidence_min is not None:
            extra_where.append("""EXISTS (
                SELECT 1 FROM detections _cf
                WHERE _cf.source_image_id = si.id AND _cf.confidence >= ?
            )""")
            params.append(confidence_min)

        if since:
            extra_where.append("si.uploaded_at >= ?")
            params.append(since)
        if until:
            extra_where.append("si.uploaded_at <= ?")
            params.append(until)
        if cursor:
            try:
                c_ts, c_id = cursor.rsplit("_", 1)
                id_val = int(c_id)
            except ValueError:
                c_ts, id_val = cursor, 0
            extra_where.append("(si.uploaded_at < ? OR (si.uploaded_at = ? AND si.id < ?))")
            params.extend([c_ts, c_ts, id_val])

        where_clause = ""
        if extra_where:
            where_clause = " AND " + " AND ".join(extra_where)

        sql = f"""SELECT si.id AS source_image_id, si.file_path, si.external_ref,
                         si.width, si.height, si.uploaded_at
                  FROM source_images si
                  WHERE si.user_id = ? AND si.environment_id = ?{where_clause}
                  ORDER BY si.uploaded_at DESC, si.id DESC LIMIT ?"""
        params.append(limit + 1)
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def list_webhooks(
    user_id: int, environment_id: int | None = None, event: str | None = None,
    active_only: bool = False,
) -> list[sqlite3.Row]:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        sql = "SELECT * FROM webhooks WHERE user_id = ? AND environment_id = ?"
        params: list = [user_id, env_id]
        if active_only:
            sql += " AND is_active = 1"
        if event:
            sql += " AND (',' || events || ',') LIKE ('%,' || ? || ',%')"
            params.append(event)
        return conn.execute(sql, params).fetchall()


def create_webhook(
    user_id: int, url: str, events: str, label: str,
    secret: str | None = None, environment_id: int | None = None,
) -> int:
    with _connect() as conn:
        env_id = _resolve_env(conn, user_id, environment_id)
        conn.execute(
            """INSERT INTO webhooks (user_id, environment_id, url, events, label, secret)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, env_id, url, events, label, secret),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_webhook(webhook_id: int, user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id),
        ).fetchone()


def update_webhook(webhook_id: int, user_id: int, **kwargs) -> bool:
    allowed = {"url", "events", "label", "secret", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(
            f"UPDATE webhooks SET {sets} WHERE id = ? AND user_id = ?",
            (*fields.values(), webhook_id, user_id),
        )
        return cur.rowcount > 0


def delete_webhook(webhook_id: int, user_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def log_delivery(
    webhook_id: int,
    event: str,
    status_code: int | None,
    duration_ms: int,
    error: str | None = None,
    is_test: int = 0,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO webhook_deliveries (webhook_id, event, status_code, duration_ms, error, is_test) "
            "VALUES (?,?,?,?,?,?)",
            (webhook_id, event, status_code, duration_ms, error, is_test),
        )
        conn.execute(
            "DELETE FROM webhook_deliveries WHERE webhook_id = ? AND id NOT IN "
            "(SELECT id FROM webhook_deliveries WHERE webhook_id = ? ORDER BY id DESC LIMIT 100)",
            (webhook_id, webhook_id),
        )


def list_deliveries(webhook_id: int, user_id: int, limit: int = 50) -> list:
    with _connect() as conn:
        return conn.execute(
            "SELECT d.* FROM webhook_deliveries d "
            "JOIN webhooks w ON w.id = d.webhook_id "
            "WHERE d.webhook_id = ? AND w.user_id = ? "
            "ORDER BY d.id DESC LIMIT ?",
            (webhook_id, user_id, limit),
        ).fetchall()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------

def export_identity_data(user_id: int, identity_ids: list[int]) -> list[dict]:
    """Return serializable data for the requested identities (owned by user_id).

    Each entry contains the identity's type/label, its face embeddings (with
    model name), and its detections (with source image path/dimensions).
    Unknown identity ids are silently skipped.
    """
    result = []
    with _connect() as conn:
        for iid in identity_ids:
            row = conn.execute(
                "SELECT * FROM identities WHERE id = ? AND user_id = ?",
                (iid, user_id),
            ).fetchone()
            if not row:
                continue

            embeddings = conn.execute(
                """SELECT fe.embedding, fe.source_image_path, m.name AS model_name
                   FROM face_embeddings fe
                   LEFT JOIN models m ON m.id = fe.model_id
                   WHERE fe.identity_id = ?""",
                (iid,),
            ).fetchall()

            detections = conn.execute(
                """SELECT d.confidence, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                          d.crop_path, d.detected_at, d.review_status,
                          si.file_path AS source_image, si.width, si.height
                   FROM detections d
                   JOIN source_images si ON si.id = d.source_image_id
                   WHERE d.identity_id = ? AND d.user_id = ?""",
                (iid, user_id),
            ).fetchall()

            result.append({
                "type": row["type"],
                "label": row["label"],
                "embeddings": [
                    {
                        "model_name": e["model_name"] or "unknown",
                        "embedding_b64": base64.b64encode(bytes(e["embedding"])).decode(),
                        "source_image": e["source_image_path"],
                    }
                    for e in embeddings
                ],
                "detections": [
                    {
                        "source_image":  d["source_image"],
                        "source_width":  d["width"],
                        "source_height": d["height"],
                        "crop":          d["crop_path"],
                        "confidence":    d["confidence"],
                        "bbox": {"x": d["bbox_x"], "y": d["bbox_y"],
                                 "w": d["bbox_w"], "h": d["bbox_h"]},
                        "detected_at":   d["detected_at"],
                        "review_status": d["review_status"],
                    }
                    for d in detections
                ],
            })
    return result


def import_identity_data(user_id: int, identities: list[dict]) -> dict:
    """Merge exported identities into this user's data (no environment scoping —
    imports land in the default scope, same as before environments were added).

    Returns a stats dict: identities_created, identities_merged,
    embeddings_imported, embeddings_skipped, detections_imported, detections_skipped.
    """
    stats = {
        "identities_created": 0,
        "identities_merged": 0,
        "embeddings_imported": 0,
        "embeddings_skipped": 0,
        "detections_imported": 0,
        "detections_skipped": 0,
    }
    with _connect() as conn:
        for id_data in identities:
            itype = id_data["type"]
            label = id_data["label"]

            existing = conn.execute(
                "SELECT id FROM identities WHERE user_id = ? AND type = ? AND label = ?",
                (user_id, itype, label),
            ).fetchone()

            if existing:
                identity_id = existing["id"]
                stats["identities_merged"] += 1
            else:
                conn.execute(
                    "INSERT INTO identities (user_id, type, label) VALUES (?, ?, ?)",
                    (user_id, itype, label),
                )
                identity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                stats["identities_created"] += 1

            for emb in id_data.get("embeddings", []):
                model_name   = emb.get("model_name", "unknown")
                source_image = emb.get("source_image")
                dup = conn.execute(
                    """SELECT fe.id FROM face_embeddings fe
                       LEFT JOIN models m ON m.id = fe.model_id
                       WHERE fe.identity_id = ? AND fe.source_image_path = ?
                         AND (m.name = ? OR (fe.model_id IS NULL AND ? = 'unknown'))""",
                    (identity_id, source_image, model_name, model_name),
                ).fetchone()
                if dup:
                    stats["embeddings_skipped"] += 1
                    continue
                model_row = conn.execute(
                    "SELECT id FROM models WHERE name = ?", (model_name,)
                ).fetchone()
                conn.execute(
                    """INSERT INTO face_embeddings
                       (identity_id, model_id, embedding, source_image_path)
                       VALUES (?, ?, ?, ?)""",
                    (identity_id, model_row["id"] if model_row else None,
                     base64.b64decode(emb["embedding_b64"]), source_image),
                )
                stats["embeddings_imported"] += 1

            for det in id_data.get("detections", []):
                source_image = det["source_image"]
                bbox = det.get("bbox", {})
                bx, by, bw, bh = bbox.get("x", 0), bbox.get("y", 0), bbox.get("w", 0), bbox.get("h", 0)
                src_row = conn.execute(
                    "SELECT id FROM source_images WHERE user_id = ? AND file_path = ?",
                    (user_id, source_image),
                ).fetchone()
                if src_row:
                    source_image_id = src_row["id"]
                else:
                    w = det.get("source_width", 0)
                    h = det.get("source_height", 0)
                    conn.execute(
                        "INSERT INTO source_images (user_id, file_path, width, height) VALUES (?,?,?,?)",
                        (user_id, source_image, w, h),
                    )
                    source_image_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                dup = conn.execute(
                    """SELECT id FROM detections
                       WHERE identity_id = ? AND user_id = ? AND source_image_id = ?
                         AND bbox_x = ? AND bbox_y = ? AND bbox_w = ? AND bbox_h = ?""",
                    (identity_id, user_id, source_image_id, bx, by, bw, bh),
                ).fetchone()
                if dup:
                    stats["detections_skipped"] += 1
                    continue
                conn.execute(
                    """INSERT INTO detections
                       (user_id, identity_id, source_image_id, type, confidence,
                        bbox_x, bbox_y, bbox_w, bbox_h, crop_path,
                        review_status, detected_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (user_id, identity_id, source_image_id, itype,
                     det.get("confidence", 0.0), bx, by, bw, bh,
                     det.get("crop", ""),
                     det.get("review_status", "confirmed"),
                     det.get("detected_at")),
                )
                stats["detections_imported"] += 1
    return stats


def _seed_models(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM models WHERE type NOT IN ('face', 'object')")
    # Insert new models; refresh the description and config on existing ones (leave
    # embedding_dim and download/active state untouched).
    conn.executemany(
        """INSERT INTO models (type, name, embedding_dim, description, config) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(type, name) DO UPDATE SET
               description = excluded.description,
               config      = excluded.config""",
        _MODEL_SEED,
    )


def _seed_settings(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO settings (key, value, value_type, category, description)
           VALUES (?, ?, ?, ?, ?)""",
        _SETTINGS_SEED,
    )
