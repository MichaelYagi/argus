"""Happy-path tests for model management endpoints."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import models as models_module
from app.core.engine_registry import registry
from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["DATA_PATH"] = str(tmp_path)
    os.environ["MODELS_PATH"] = str(tmp_path / "models")
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    models_module._loaded.clear()
    models_module._progress.clear()
    registry.swap_face_engine(None)
    registry.swap_object_engine(None)
    for k in ("SECRET_KEY", "DATA_PATH", "MODELS_PATH"):
        os.environ.pop(k, None)


def _setup(client) -> dict:
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return {"X-API-Key": key}


def _model_id(name: str) -> int:
    with store._connect() as conn:
        return conn.execute("SELECT id FROM models WHERE name = ?", (name,)).fetchone()[0]


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------

def test_list_models_returns_all(client):
    h = _setup(client)
    r = client.get("/api/models", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 11


def test_list_models_filter_face(client):
    h = _setup(client)
    r = client.get("/api/models?type=face", headers=h)
    names = {m["name"] for m in r.json()}
    assert names == {"buffalo_l", "buffalo_s", "antelopev2"}


def test_list_models_filter_object(client):
    h = _setup(client)
    r = client.get("/api/models?type=object", headers=h)
    names = {m["name"] for m in r.json()}
    assert names == {"yolov8n", "yolov8s", "yolov8m", "yolov8x", "yolo11n",
                     "yolov8s-worldv2", "yolov8m-worldv2", "yolov8l-worldv2"}


def test_get_model_detail(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    r = client.get(f"/api/models/{mid}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "buffalo_l"
    assert data["is_downloaded"] is False
    assert data["is_active"] is False
    assert data["embedding_dim"] == 512


def test_get_model_not_found(client):
    h = _setup(client)
    r = client.get("/api/models/999", headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def test_download_triggers_background_task(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    with patch("app.api.models._run_download") as mock_dl:
        r = client.post(f"/api/models/{mid}/download", headers=h)
    assert r.status_code == 202
    assert r.json()["status"] == "downloading"
    mock_dl.assert_called_once()


def test_download_already_downloaded(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    store.set_model_downloaded(mid, True)
    r = client.post(f"/api/models/{mid}/download", headers=h)
    assert r.status_code == 202
    assert r.json()["status"] == "already_downloaded"


def test_download_status_idle(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    r = client.get(f"/api/models/{mid}/download/status", headers=h)
    assert r.json()["status"] == "idle"


def test_download_status_complete_when_downloaded(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    store.set_model_downloaded(mid, True)
    r = client.get(f"/api/models/{mid}/download/status", headers=h)
    assert r.json()["status"] == "complete"


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------

def test_activate_requires_download(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    r = client.put(f"/api/models/{mid}/activate", headers=h)
    assert r.status_code == 409


def test_activate_face_model(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    store.set_model_downloaded(mid, True)

    mock_engine = MagicMock()
    with patch("app.api.models._load_engine", return_value=mock_engine):
        r = client.put(f"/api/models/{mid}/activate", headers=h)

    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert registry.get_face_engine() is mock_engine


def test_activate_uses_cached_engine(client):
    h = _setup(client)
    mid = _model_id("buffalo_l")
    store.set_model_downloaded(mid, True)

    cached = MagicMock(name="cached")
    models_module._loaded[mid] = cached

    with patch("app.api.models._load_engine") as mock_load:
        client.put(f"/api/models/{mid}/activate", headers=h)
        mock_load.assert_not_called()

    assert registry.get_face_engine() is cached


def test_activate_swaps_previous_engine(client):
    h = _setup(client)
    bl = _model_id("buffalo_l")
    bs = _model_id("buffalo_s")
    store.set_model_downloaded(bl, True)
    store.set_model_downloaded(bs, True)

    e1, e2 = MagicMock(), MagicMock()
    with patch("app.api.models._load_engine", return_value=e1):
        client.put(f"/api/models/{bl}/activate", headers=h)
    with patch("app.api.models._load_engine", return_value=e2):
        client.put(f"/api/models/{bs}/activate", headers=h)

    assert registry.get_face_engine() is e2
    assert store.get_model(bs)["is_active"] == 1
    assert store.get_model(bl)["is_active"] == 0


def test_activate_object_model(client):
    h = _setup(client)
    mid = _model_id("yolov8n")
    store.set_model_downloaded(mid, True)

    mock_engine = MagicMock()
    with patch("app.api.models._load_engine", return_value=mock_engine):
        r = client.put(f"/api/models/{mid}/activate", headers=h)

    assert r.status_code == 200
    assert registry.get_object_engine() is mock_engine


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_model(client):
    h = _setup(client)
    mid = _model_id("yolov8n")
    store.set_model_downloaded(mid, True)

    with patch("app.api.models._delete_files"):
        r = client.delete(f"/api/models/{mid}", headers=h)

    assert r.status_code == 204
    assert store.get_model(mid)["is_downloaded"] == 0


def test_delete_active_model_unloads_engine(client):
    h = _setup(client)
    mid = _model_id("yolov8n")
    store.set_model_downloaded(mid, True)

    mock_engine = MagicMock()
    with patch("app.api.models._load_engine", return_value=mock_engine):
        client.put(f"/api/models/{mid}/activate", headers=h)

    assert registry.get_object_engine() is mock_engine

    with patch("app.api.models._delete_files"):
        client.delete(f"/api/models/{mid}", headers=h)

    assert registry.get_object_engine() is None


def test_delete_not_downloaded_returns_409(client):
    h = _setup(client)
    mid = _model_id("yolov8n")
    r = client.delete(f"/api/models/{mid}", headers=h)
    assert r.status_code == 409
