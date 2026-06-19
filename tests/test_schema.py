"""Schema application test: fresh DB, apply schema, confirm structure and seed data."""

import pytest

from app.db import store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    store.configure(tmp_path / "argus_test.db")
    store.init_db()
    yield
    store.configure(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query(sql: str, params: tuple = ()) -> list:
    with store._connect() as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_all_tables_exist():
    tables = {r[0] for r in _query("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "users", "api_keys",
        "identities", "face_embeddings", "source_images", "detections",
        "models", "settings",
    }
    assert expected <= tables


def test_detections_indexes_exist():
    indexes = {r[0] for r in _query(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='detections'"
    )}
    assert {
        "idx_detections_user_identity",
        "idx_detections_user_type",
        "idx_detections_review",
        "idx_detections_source_image",
    } <= indexes


def test_face_embeddings_model_index_exists():
    indexes = {r[0] for r in _query(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='face_embeddings'"
    )}
    assert "idx_face_embeddings_model" in indexes


def test_api_keys_hash_index_exists():
    indexes = {r[0] for r in _query(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='api_keys'"
    )}
    assert "idx_api_keys_hash" in indexes


# ---------------------------------------------------------------------------
# Seed data — models
# ---------------------------------------------------------------------------

def test_models_seeded_count():
    rows = _query("SELECT * FROM models")
    assert len(rows) == 8


def test_models_seeded_face_entries():
    rows = _query("SELECT name, embedding_dim FROM models WHERE type='face' ORDER BY name")
    names = {r["name"] for r in rows}
    assert names == {"buffalo_l", "buffalo_s", "antelopev2"}
    assert all(r["embedding_dim"] == 512 for r in rows)


def test_models_seeded_object_entries():
    rows = _query("SELECT name, embedding_dim FROM models WHERE type='object' ORDER BY name")
    names = {r["name"] for r in rows}
    assert names == {"yolov8n", "yolov8s", "yolov8m", "yolov8x", "yolo11n"}
    assert all(r["embedding_dim"] is None for r in rows)


def test_no_models_active_by_default():
    rows = _query("SELECT COUNT(*) FROM models WHERE is_active=1")
    assert rows[0][0] == 0


def test_no_models_downloaded_by_default():
    rows = _query("SELECT COUNT(*) FROM models WHERE is_downloaded=1")
    assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# Seed data — settings
# ---------------------------------------------------------------------------

def test_settings_seeded_count():
    rows = _query("SELECT * FROM settings")
    assert len(rows) == 14


def test_settings_seeded_spot_check():
    by_key = {r["key"]: r for r in _query("SELECT * FROM settings")}

    assert by_key["face.match_threshold"]["value"] == "0.5"
    assert by_key["face.match_threshold"]["value_type"] == "float"
    assert by_key["face.match_threshold"]["category"] == "face"

    assert by_key["system.crop_padding"]["value"] == "0.2"
    assert by_key["system.crop_padding"]["value_type"] == "float"

    assert by_key["object.classes_enabled"]["value"] == "*"
    assert by_key["object.classes_enabled"]["value_type"] == "string"

    assert by_key["system.save_unknown_detections"]["value"] == "true"
    assert by_key["system.save_unknown_detections"]["value_type"] == "bool"

    assert by_key["system.use_gpu"]["value"] == "true"
    assert by_key["system.use_gpu"]["value_type"] == "bool"


def test_settings_categories():
    by_category: dict[str, list] = {}
    for r in _query("SELECT category FROM settings"):
        by_category.setdefault(r["category"], []).append(r)
    assert set(by_category.keys()) == {"face", "object", "system"}
    assert len(by_category["face"]) == 5
    assert len(by_category["object"]) == 3
    assert len(by_category["system"]) == 6


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_init_db_idempotent():
    store.init_db()  # second call on same DB
    assert len(_query("SELECT * FROM models")) == 8
    assert len(_query("SELECT * FROM settings")) == 14
