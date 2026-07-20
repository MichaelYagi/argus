"""Tests for /api/environments CRUD."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["DATA_PATH"] = str(tmp_path)
    os.environ["SECRET_KEY"] = "test-secret"
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("DATA_PATH", None)
    os.environ.pop("SECRET_KEY", None)


def _create_user_and_key(username: str = "tester") -> tuple[int, str]:
    from app.core.security import hash_password
    user_id = store.create_user(username, hash_password("pass"))
    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), "key")
    return user_id, plaintext


def test_environments_requires_auth(client):
    r = client.get("/api/environments")
    assert r.status_code in (401, 403)


def test_list_environments_returns_default(client):
    _, key = _create_user_and_key()
    r = client.get("/api/environments", headers={"X-API-Key": key})
    assert r.status_code == 200
    envs = r.json()
    assert len(envs) >= 1
    assert any(e["name"] == "default" for e in envs)


def test_create_environment(client):
    _, key = _create_user_and_key()
    r = client.post("/api/environments", json={"name": "production"},
                    headers={"X-API-Key": key})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "production"
    assert "id" in data


def test_create_environment_duplicate_409(client):
    _, key = _create_user_and_key()
    client.post("/api/environments", json={"name": "staging"}, headers={"X-API-Key": key})
    r = client.post("/api/environments", json={"name": "staging"}, headers={"X-API-Key": key})
    assert r.status_code == 409


def test_create_environment_empty_name_400(client):
    _, key = _create_user_and_key()
    r = client.post("/api/environments", json={"name": "  "}, headers={"X-API-Key": key})
    assert r.status_code == 400


def test_get_environment(client):
    _, key = _create_user_and_key()
    created = client.post("/api/environments", json={"name": "test-env"},
                          headers={"X-API-Key": key}).json()
    r = client.get(f"/api/environments/{created['id']}", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["name"] == "test-env"


def test_get_environment_404(client):
    _, key = _create_user_and_key()
    r = client.get("/api/environments/9999", headers={"X-API-Key": key})
    assert r.status_code == 404


def test_rename_environment(client):
    _, key = _create_user_and_key()
    created = client.post("/api/environments", json={"name": "old-name"},
                          headers={"X-API-Key": key}).json()
    r = client.put(f"/api/environments/{created['id']}", json={"name": "new-name"},
                   headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_rename_environment_404(client):
    _, key = _create_user_and_key()
    r = client.put("/api/environments/9999", json={"name": "x"}, headers={"X-API-Key": key})
    assert r.status_code == 404


def test_delete_environment(client):
    _, key = _create_user_and_key()
    created = client.post("/api/environments", json={"name": "to-delete"},
                          headers={"X-API-Key": key}).json()
    r = client.delete(f"/api/environments/{created['id']}", headers={"X-API-Key": key})
    assert r.status_code == 204


def test_delete_only_environment_400(client):
    user_id, key = _create_user_and_key()
    envs = client.get("/api/environments", headers={"X-API-Key": key}).json()
    only_id = envs[0]["id"]
    r = client.delete(f"/api/environments/{only_id}", headers={"X-API-Key": key})
    assert r.status_code == 400


def test_environments_isolated_between_users(client):
    _, key1 = _create_user_and_key("u1")
    _, key2 = _create_user_and_key("u2")
    client.post("/api/environments", json={"name": "u1-env"}, headers={"X-API-Key": key1})
    envs2 = client.get("/api/environments", headers={"X-API-Key": key2}).json()
    assert all(e["name"] != "u1-env" for e in envs2)


def test_delete_environment_preserves_file_shared_with_other_env(client):
    """Deleting an environment must not queue a source file for deletion when another env references it."""
    user_id, _ = _create_user_and_key("env-del-test")

    env0_id = store.get_default_environment_id(user_id) or 0
    env1_id = store.create_environment(user_id, "env1")

    iid0, _ = store.get_or_create_identity(user_id, "face", "Shared", environment_id=env0_id)
    iid1, _ = store.get_or_create_identity(user_id, "face", "Shared", environment_id=env1_id)

    shared_file = "shared_env_file.jpg"
    for env_id, iid in [(env0_id, iid0), (env1_id, iid1)]:
        with store._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO source_images"
                " (user_id, environment_id, file_path, width, height) VALUES (?, ?, ?, 640, 480)",
                (user_id, env_id, shared_file),
            )
            src_id = conn.execute(
                "SELECT id FROM source_images WHERE user_id = ? AND environment_id = ? AND file_path = ?",
                (user_id, env_id, shared_file),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO detections
                   (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                    bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
                   VALUES (?, ?, ?, ?, 'face', NULL, 0.9, 0, 0, 100, 100, 'crop.jpg')""",
                (user_id, env_id, iid, src_id),
            )

    _deleted, _crops, sources = store.delete_environment(env0_id, user_id)

    # env1 still references shared_file — it must not be queued for deletion.
    assert shared_file not in sources
