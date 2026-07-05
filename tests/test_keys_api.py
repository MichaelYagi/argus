"""Tests for GET/POST/PUT/DELETE /api/keys."""

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
    store.create_api_key(user_id, hash_api_key(plaintext), "seed key")
    return user_id, plaintext


def test_keys_requires_auth(client):
    r = client.get("/api/keys")
    assert r.status_code in (401, 403)


def test_list_keys(client):
    _, key = _create_user_and_key()
    r = client.get("/api/keys", headers={"X-API-Key": key})
    assert r.status_code == 200
    keys = r.json()
    assert len(keys) >= 1
    k = keys[0]
    assert "id" in k and "label" in k and "is_active" in k
    assert "key" not in k  # plaintext never returned on list


def test_create_key_returns_plaintext_once(client):
    _, key = _create_user_and_key()
    r = client.post("/api/keys", json={"label": "new key"}, headers={"X-API-Key": key})
    assert r.status_code == 201
    data = r.json()
    assert "key" in data
    assert data["key"].startswith("argus_")
    assert data["label"] == "new key"


def test_create_key_with_environment(client):
    user_id, key = _create_user_and_key()
    env_id = store.create_environment(user_id, "prod")
    r = client.post("/api/keys", json={"label": "prod key", "environment_id": env_id},
                    headers={"X-API-Key": key})
    assert r.status_code == 201
    data = r.json()
    assert data["environment_id"] == env_id
    assert data["environment_name"] == "prod"


def test_rename_key(client):
    _, key = _create_user_and_key()
    keys = client.get("/api/keys", headers={"X-API-Key": key}).json()
    key_id = keys[0]["id"]
    r = client.put(f"/api/keys/{key_id}", json={"label": "renamed"},
                   headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["label"] == "renamed"


def test_rename_key_empty_label_400(client):
    _, key = _create_user_and_key()
    keys = client.get("/api/keys", headers={"X-API-Key": key}).json()
    key_id = keys[0]["id"]
    r = client.put(f"/api/keys/{key_id}", json={"label": "  "}, headers={"X-API-Key": key})
    assert r.status_code == 400


def test_rename_key_404(client):
    _, key = _create_user_and_key()
    r = client.put("/api/keys/9999", json={"label": "x"}, headers={"X-API-Key": key})
    assert r.status_code == 404


def test_revoke_key_404(client):
    _, key = _create_user_and_key()
    r = client.delete("/api/keys/9999", headers={"X-API-Key": key})
    assert r.status_code == 404


def test_revoked_key_no_longer_works(client):
    _, key = _create_user_and_key()
    new = client.post("/api/keys", json={"label": "temp"}, headers={"X-API-Key": key}).json()
    new_key = new["key"]
    key_id = new["id"]

    assert client.get("/api/keys", headers={"X-API-Key": new_key}).status_code == 200
    client.delete(f"/api/keys/{key_id}", headers={"X-API-Key": key})
    assert client.get("/api/keys", headers={"X-API-Key": new_key}).status_code in (401, 403)


def test_keys_isolated_between_users(client):
    _, key1 = _create_user_and_key("u1")
    _, key2 = _create_user_and_key("u2")
    keys1 = client.get("/api/keys", headers={"X-API-Key": key1}).json()
    keys2 = client.get("/api/keys", headers={"X-API-Key": key2}).json()
    ids1 = {k["id"] for k in keys1}
    ids2 = {k["id"] for k in keys2}
    assert ids1.isdisjoint(ids2)
