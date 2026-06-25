-- users: accounts for sign-in and API key ownership
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_approved   INTEGER NOT NULL DEFAULT 1,  -- 0 = pending admin review
    timezone      TEXT    NOT NULL DEFAULT 'UTC',
    locale        TEXT    NOT NULL DEFAULT 'en-US',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- api_keys: per-user API keys; plaintext shown once, only hash stored
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id INTEGER NOT NULL DEFAULT 0,
    key_hash     TEXT    NOT NULL UNIQUE,
    label        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- identities: per-user enrolled people (faces) and tracked object classes
CREATE TABLE IF NOT EXISTS identities (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id     INTEGER NOT NULL DEFAULT 0,
    type               TEXT    NOT NULL CHECK(type IN ('face', 'object')),
    label              TEXT    NOT NULL,
    cover_detection_id      INTEGER REFERENCES detections(id) ON DELETE SET NULL,
    representative_embedding BLOB,   -- mean of all face_embeddings for the active model
    external_ref       TEXT,        -- opaque caller-owned correlation id; never interpreted by Argus
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, environment_id, type, label)
);

-- source_images: per-user uploaded images; file_path is content-hash based
-- (same file uploaded by two users shares the file on disk, separate DB rows)
CREATE TABLE IF NOT EXISTS source_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id INTEGER NOT NULL DEFAULT 0,
    file_path   TEXT    NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    external_ref TEXT,             -- opaque caller-owned correlation id; never interpreted by Argus
    uploaded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, environment_id, file_path)
);

-- face_embeddings: reference embeddings per enrolled face identity, tagged by model.
-- user_id is implicit via identities.user_id — no direct column needed.
CREATE TABLE IF NOT EXISTS face_embeddings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id       INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    environment_id    INTEGER NOT NULL DEFAULT 0,
    model_id          INTEGER          REFERENCES models(id),
    embedding         BLOB    NOT NULL,
    source_image_path TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Index on (model_id, identity_id): hot path for recognition — filter by active
-- model before comparing embeddings.
CREATE INDEX IF NOT EXISTS idx_face_embeddings_model
    ON face_embeddings(model_id, identity_id);

-- detections: per-user face/object hits from any detect call
CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id  INTEGER NOT NULL DEFAULT 0,
    identity_id     INTEGER REFERENCES identities(id) ON DELETE SET NULL,
    source_image_id INTEGER NOT NULL REFERENCES source_images(id) ON DELETE CASCADE,
    type            TEXT    NOT NULL CHECK(type IN ('face', 'object')),
    model_id        INTEGER REFERENCES models(id),
    confidence      REAL    NOT NULL,
    bbox_x          INTEGER NOT NULL,
    bbox_y          INTEGER NOT NULL,
    bbox_w          INTEGER NOT NULL,
    bbox_h          INTEGER NOT NULL,
    crop_path       TEXT    NOT NULL,
    embedding       BLOB,              -- face detections only; used for review-queue suggested matches
    review_status   TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(review_status IN ('pending', 'confirmed', 'rejected', 'reassigned')),
    ignored         INTEGER NOT NULL DEFAULT 0,   -- dismissed from Suggested people; row kept
    reviewed_at     TEXT,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_detections_user_identity
    ON detections(user_id, identity_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_user_type
    ON detections(user_id, type, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_review
    ON detections(user_id, review_status, type, confidence);
CREATE INDEX IF NOT EXISTS idx_detections_source_image
    ON detections(source_image_id);

-- models: shared registry of available face/object models (admin-managed)
CREATE TABLE IF NOT EXISTS models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type          TEXT    NOT NULL CHECK(type IN ('face', 'object')),
    name          TEXT    NOT NULL,
    file_path     TEXT,
    embedding_dim INTEGER,
    is_downloaded INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(type, name)
);

-- jobs: async detection job queue — created by ?async=true detect calls, polled via /api/jobs
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id INTEGER NOT NULL DEFAULT 0,
    type        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'running', 'done', 'failed')),
    result      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at DESC);

-- environments: isolated data namespaces per user
CREATE TABLE IF NOT EXISTS environments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_environments_user ON environments(user_id);

-- settings: shared key-value config for thresholds and behaviour knobs
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL CHECK(value_type IN ('float', 'int', 'bool', 'string')),
    category    TEXT NOT NULL,
    description TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- changes: append-only feed of mutations so clients can sync deltas via
-- GET /api/changes?since=<id>. The autoincrement id is the monotonic cursor.
-- Generic recognition events — entity_type/action are domain terms, not client-specific.
CREATE TABLE IF NOT EXISTS changes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id INTEGER NOT NULL DEFAULT 0,
    entity_type    TEXT    NOT NULL,   -- 'identity' | 'detection'
    entity_id      INTEGER NOT NULL,
    action         TEXT    NOT NULL,   -- 'created' | 'relabeled' | 'deleted'
    external_ref   TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_changes_user_env ON changes(user_id, environment_id, id);
