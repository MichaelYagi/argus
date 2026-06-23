"""Happy-path tests for settings list, get, update, and reset."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core import settings_cache
from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)


def _setup(client) -> dict:
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------

def test_list_settings_grouped(client):
    h = _setup(client)
    r = client.get("/api/settings", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"face", "object", "system"}
    assert len(data["face"]) == 7
    assert len(data["object"]) == 4
    assert len(data["system"]) == 6


def test_list_settings_values_are_typed(client):
    h = _setup(client)
    r = client.get("/api/settings", headers=h)
    by_key = {s["key"]: s for cat in r.json().values() for s in cat}
    assert isinstance(by_key["face.match_threshold"]["value"], float)
    assert isinstance(by_key["face.min_face_size"]["value"], int)
    assert isinstance(by_key["system.save_unknown_detections"]["value"], bool)
    assert isinstance(by_key["object.classes_enabled"]["value"], str)


# ---------------------------------------------------------------------------
# GET /api/settings/{key}
# ---------------------------------------------------------------------------

def test_get_single_setting(client):
    h = _setup(client)
    r = client.get("/api/settings/face.match_threshold", headers=h)
    assert r.status_code == 200
    assert r.json()["key"] == "face.match_threshold"
    assert r.json()["value"] == 0.5


def test_get_setting_not_found(client):
    h = _setup(client)
    r = client.get("/api/settings/nonexistent.key", headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/settings/{key}
# ---------------------------------------------------------------------------

def test_update_float_setting(client):
    h = _setup(client)
    r = client.put("/api/settings/face.match_threshold", json={"value": 0.7}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] == 0.7
    # Settings cache updated immediately
    assert settings_cache.cache.get("face.match_threshold") == 0.7


def test_update_int_setting(client):
    h = _setup(client)
    r = client.put("/api/settings/face.min_face_size", json={"value": 60}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] == 60
    assert settings_cache.cache.get("face.min_face_size") == 60


def test_update_bool_setting(client):
    h = _setup(client)
    r = client.put("/api/settings/system.save_unknown_detections", json={"value": "false"}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] is False
    assert settings_cache.cache.get("system.save_unknown_detections") is False


def test_update_string_setting(client):
    h = _setup(client)
    r = client.put("/api/settings/object.classes_enabled", json={"value": "dog,cat"}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] == "dog,cat"


def test_update_rejects_invalid_float(client):
    h = _setup(client)
    r = client.put("/api/settings/face.match_threshold", json={"value": "not-a-number"}, headers=h)
    assert r.status_code == 400


def test_update_rejects_invalid_bool(client):
    h = _setup(client)
    r = client.put("/api/settings/system.save_unknown_detections", json={"value": "yes"}, headers=h)
    assert r.status_code == 400


def test_update_use_gpu_true_rejects_when_no_gpu(client):
    h = _setup(client)
    with patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]):
        r = client.put("/api/settings/system.use_gpu", json={"value": "true"}, headers=h)
    assert r.status_code == 400


def test_update_use_gpu_true_accepts_when_gpu_present(client):
    h = _setup(client)
    with patch("onnxruntime.get_available_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]):
        r = client.put("/api/settings/system.use_gpu", json={"value": "true"}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] is True


def test_update_use_gpu_false_always_accepted(client):
    h = _setup(client)
    r = client.put("/api/settings/system.use_gpu", json={"value": "false"}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] is False


# ---------------------------------------------------------------------------
# POST /api/settings/reset
# ---------------------------------------------------------------------------

def test_reset_single_key(client):
    h = _setup(client)
    client.put("/api/settings/face.match_threshold", json={"value": 0.9}, headers=h)
    assert settings_cache.cache.get("face.match_threshold") == 0.9

    r = client.post("/api/settings/reset?key=face.match_threshold", headers=h)
    assert r.status_code == 200
    assert r.json()[0]["value"] == 0.5
    assert settings_cache.cache.get("face.match_threshold") == 0.5


def test_reset_category(client):
    h = _setup(client)
    client.put("/api/settings/face.match_threshold", json={"value": 0.9}, headers=h)
    client.put("/api/settings/face.min_face_size", json={"value": 100}, headers=h)

    r = client.post("/api/settings/reset?category=face", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 7
    assert settings_cache.cache.get("face.match_threshold") == 0.5
    assert settings_cache.cache.get("face.min_face_size") == 40


def test_reset_requires_key_or_category(client):
    h = _setup(client)
    r = client.post("/api/settings/reset", headers=h)
    assert r.status_code == 400


def test_reset_rejects_both_key_and_category(client):
    h = _setup(client)
    r = client.post("/api/settings/reset?key=face.match_threshold&category=face", headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# face.match_strategy choice
# ---------------------------------------------------------------------------

def test_match_strategy_default_is_best(client):
    h = _setup(client)
    r = client.get("/api/settings/face.match_strategy", headers=h)
    assert r.status_code == 200
    assert r.json()["value"] == "best"


def test_match_strategy_accepts_average(client):
    h = _setup(client)
    r = client.put("/api/settings/face.match_strategy", json={"value": "average"}, headers=h)
    assert r.status_code == 200
    assert r.json()["value"] == "average"


def test_match_strategy_rejects_invalid(client):
    h = _setup(client)
    r = client.put("/api/settings/face.match_strategy", json={"value": "fuzzy"}, headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Admin-only gating
# ---------------------------------------------------------------------------

def _nonadmin(client) -> dict:
    from app.core.security import hash_password
    uid = store.create_user("bob", hash_password("pass12345"), is_admin=False)
    key = generate_api_key()
    store.create_api_key(uid, hash_api_key(key), "bob")
    return {"X-API-Key": key}


def test_settings_list_forbidden_for_nonadmin(client):
    _setup(client)  # alice is admin (first user)
    h = _nonadmin(client)
    assert client.get("/api/settings", headers=h).status_code == 403


def test_settings_update_forbidden_for_nonadmin(client):
    _setup(client)
    h = _nonadmin(client)
    r = client.put("/api/settings/face.match_threshold", json={"value": 0.7}, headers=h)
    assert r.status_code == 403
