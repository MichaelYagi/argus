-- identities: unified table for enrolled people (faces) and tracked object classes
CREATE TABLE IF NOT EXISTS identities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT    NOT NULL CHECK(type IN ('face', 'object')),
    label      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(type, label)
);

-- source_images: stable handle for an uploaded image; multiple detections share one row
CREATE TABLE IF NOT EXISTS source_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT    NOT NULL UNIQUE,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    uploaded_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- models: registry of available face/object models (downloaded weights + active state)
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

-- face_embeddings: reference embeddings per enrolled face identity, tagged by model
CREATE TABLE IF NOT EXISTS face_embeddings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id       INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    model_id          INTEGER          REFERENCES models(id),
    embedding         BLOB    NOT NULL,
    source_image_path TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Index on (model_id, identity_id): the recognition hot path filters by active model_id
-- before comparing embeddings. Not specified in DESIGN.md §4 but clearly required.
CREATE INDEX IF NOT EXISTS idx_face_embeddings_model
    ON face_embeddings(model_id, identity_id);

-- detections: every face/object hit from any detect call
CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
    review_status   TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(review_status IN ('pending', 'confirmed', 'rejected', 'reassigned')),
    reviewed_at     TEXT,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_detections_identity
    ON detections(identity_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_type
    ON detections(type, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_review
    ON detections(review_status, type, confidence);
CREATE INDEX IF NOT EXISTS idx_detections_source_image
    ON detections(source_image_id);

-- settings: key-value config for thresholds and behaviour knobs (not model selection)
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL CHECK(value_type IN ('float', 'int', 'bool', 'string')),
    category    TEXT NOT NULL,
    description TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
