"""Tests for GET /api/changes."""

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


def test_changes_requires_auth(client):
    r = client.get("/api/changes")
    assert r.status_code in (401, 403)


def test_changes_empty_initially(client):
    _, key = _create_user_and_key()
    r = client.get("/api/changes", headers={"X-API-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["has_more"] is False
    assert "next_cursor" in data


def test_changes_returns_after_identity_create(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Alice", env_id)

    r = client.get("/api/changes", headers={"X-API-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) >= 1
    item = data["items"][0]
    assert item["entity_type"] == "identity"
    assert item["action"] == "created"


def test_changes_since_filters_old(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Bob", env_id)

    r1 = client.get("/api/changes", headers={"X-API-Key": key})
    cursor = r1.json()["next_cursor"]

    store.create_identity(user_id, "face", "Carol", env_id)

    r2 = client.get(f"/api/changes?since={cursor}", headers={"X-API-Key": key})
    items = r2.json()["items"]
    labels = [i["entity_type"] for i in items]
    assert len(items) >= 1
    # Only events after the cursor
    assert all(i["id"] > cursor for i in items)


def test_changes_isolated_between_users(client):
    user_id1, key1 = _create_user_and_key("user1")
    user_id2, key2 = _create_user_and_key("user2")
    env_id1 = store.get_default_environment_id(user_id1)
    store.create_identity(user_id1, "face", "Alice", env_id1)

    r = client.get("/api/changes", headers={"X-API-Key": key2})
    assert r.json()["items"] == []
