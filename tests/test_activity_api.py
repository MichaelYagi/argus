"""Tests for GET /api/activity."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key, hash_password
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


def _admin_session(client):
    user_id = store.create_user("admin", hash_password("pass"), is_admin=True)
    client.post("/login", data={"username": "admin", "password": "pass"})
    return user_id


def _non_admin_key() -> tuple[int, str]:
    user_id = store.create_user("regular", hash_password("pass"))
    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), "key")
    return user_id, plaintext


def test_activity_requires_admin(client):
    _, key = _non_admin_key()
    r = client.get("/api/activity", headers={"X-API-Key": key})
    assert r.status_code in (401, 403)


def test_activity_returns_shape(client):
    _admin_session(client)
    r = client.get("/api/activity")
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "buffer_size" in data
    assert isinstance(data["events"], list)


def test_activity_limit_param(client):
    from app.core import activity_buffer
    activity_buffer.emit("test", "event one")
    activity_buffer.emit("test", "event two")
    _admin_session(client)
    r = client.get("/api/activity?limit=1")
    assert r.status_code == 200
    assert len(r.json()["events"]) <= 1
