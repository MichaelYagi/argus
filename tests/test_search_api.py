"""Tests for GET /api/search."""

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


def test_search_requires_auth(client):
    r = client.get("/api/search?q=alice")
    assert r.status_code in (401, 403)


def test_search_requires_q(client):
    _, key = _create_user_and_key()
    r = client.get("/api/search", headers={"X-API-Key": key})
    assert r.status_code == 422


def test_search_empty_returns_no_results(client):
    _, key = _create_user_and_key()
    r = client.get("/api/search?q=nobody", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_search_finds_identity(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Alice Smith", env_id)
    r = client.get("/api/search?q=alice", headers={"X-API-Key": key})
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["label"] == "Alice Smith" for i in items)


def test_search_type_filter_face(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Bob", env_id)
    store.create_identity(user_id, "object", "Bob-object", env_id)
    r = client.get("/api/search?q=bob&type=face", headers={"X-API-Key": key})
    items = r.json()["items"]
    assert all(i["type"] == "face" for i in items)


def test_search_type_filter_object(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Carol", env_id)
    store.create_identity(user_id, "object", "Carol-thing", env_id)
    r = client.get("/api/search?q=carol&type=object", headers={"X-API-Key": key})
    items = r.json()["items"]
    assert all(i["type"] == "object" for i in items)


def test_search_invalid_type_400(client):
    _, key = _create_user_and_key()
    r = client.get("/api/search?q=x&type=bogus", headers={"X-API-Key": key})
    assert r.status_code == 400


def test_search_result_shape(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Dave", env_id)
    r = client.get("/api/search?q=dave", headers={"X-API-Key": key})
    item = r.json()["items"][0]
    assert "id" in item
    assert "label" in item
    assert "type" in item
    assert "detection_count" in item


def test_search_isolated_between_users(client):
    user_id1, key1 = _create_user_and_key("u1")
    _, key2 = _create_user_and_key("u2")
    env_id = store.get_default_environment_id(user_id1)
    store.create_identity(user_id1, "face", "UniqueNameXYZ", env_id)
    r = client.get("/api/search?q=UniqueNameXYZ", headers={"X-API-Key": key2})
    assert r.json()["items"] == []
