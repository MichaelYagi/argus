"""Tests for webhook CRUD, delivery log, and test-ping endpoints."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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


def _setup(client) -> tuple[int, dict]:
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("pass12345"))
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return user_id, {"X-API-Key": key}


def _create(client, headers, **kwargs) -> dict:
    body = {"url": "http://example.com/hook", "events": ["job.done"], **kwargs}
    r = client.post("/api/webhooks", json=body, headers=headers)
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# GET /api/webhooks
# ---------------------------------------------------------------------------

def test_list_webhooks_empty(client):
    _, h = _setup(client)
    r = client.get("/api/webhooks", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_list_webhooks_returns_created(client):
    _, h = _setup(client)
    _create(client, h, label="one")
    _create(client, h, label="two")
    r = client.get("/api/webhooks", headers=h)
    assert r.status_code == 200
    labels = {w["label"] for w in r.json()}
    assert labels == {"one", "two"}


# ---------------------------------------------------------------------------
# POST /api/webhooks
# ---------------------------------------------------------------------------

def test_create_webhook_201(client):
    _, h = _setup(client)
    r = client.post("/api/webhooks", json={
        "url": "http://example.com/hook",
        "events": ["job.done"],
        "label": "my hook",
    }, headers=h)
    assert r.status_code == 201
    data = r.json()
    assert data["url"] == "http://example.com/hook"
    assert data["events"] == ["job.done"]
    assert data["label"] == "my hook"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


def test_create_webhook_both_events(client):
    _, h = _setup(client)
    r = client.post("/api/webhooks", json={
        "url": "http://example.com/hook",
        "events": ["detection.created", "job.done"],
    }, headers=h)
    assert r.status_code == 201
    # Events are sorted
    assert r.json()["events"] == ["detection.created", "job.done"]


def test_create_webhook_deduplicates_events(client):
    _, h = _setup(client)
    r = client.post("/api/webhooks", json={
        "url": "http://example.com/hook",
        "events": ["job.done", "job.done"],
    }, headers=h)
    assert r.status_code == 201
    assert r.json()["events"] == ["job.done"]


def test_create_webhook_unknown_event_400(client):
    _, h = _setup(client)
    r = client.post("/api/webhooks", json={
        "url": "http://example.com/hook",
        "events": ["not.an.event"],
    }, headers=h)
    assert r.status_code == 400


def test_create_webhook_requires_auth(client):
    r = client.post("/api/webhooks", json={"url": "http://example.com/hook"})
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/webhooks/{id}
# ---------------------------------------------------------------------------

def test_get_webhook_200(client):
    _, h = _setup(client)
    created = _create(client, h, label="alpha")
    r = client.get(f"/api/webhooks/{created['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["label"] == "alpha"


def test_get_webhook_404(client):
    _, h = _setup(client)
    r = client.get("/api/webhooks/9999", headers=h)
    assert r.status_code == 404


def test_get_webhook_cross_user_isolation(client):
    from app.core.security import hash_password
    _, h1 = _setup(client)
    created = _create(client, h1)

    uid2 = store.create_user("bob", hash_password("pass12345"))
    key2 = generate_api_key()
    store.create_api_key(uid2, hash_api_key(key2), "bob")
    h2 = {"X-API-Key": key2}

    r = client.get(f"/api/webhooks/{created['id']}", headers=h2)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/webhooks/{id}
# ---------------------------------------------------------------------------

def test_update_webhook_url(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.put(f"/api/webhooks/{w['id']}", json={"url": "http://new.example.com/hook"}, headers=h)
    assert r.status_code == 200
    assert r.json()["url"] == "http://new.example.com/hook"


def test_update_webhook_label(client):
    _, h = _setup(client)
    w = _create(client, h, label="old")
    r = client.put(f"/api/webhooks/{w['id']}", json={"label": "new"}, headers=h)
    assert r.status_code == 200
    assert r.json()["label"] == "new"


def test_update_webhook_is_active_false(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.put(f"/api/webhooks/{w['id']}", json={"is_active": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_update_webhook_events(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.put(f"/api/webhooks/{w['id']}", json={"events": ["detection.created"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["events"] == ["detection.created"]


def test_update_webhook_invalid_event_400(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.put(f"/api/webhooks/{w['id']}", json={"events": ["bogus.event"]}, headers=h)
    assert r.status_code == 400


def test_update_webhook_404(client):
    _, h = _setup(client)
    r = client.put("/api/webhooks/9999", json={"label": "x"}, headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/webhooks/{id}
# ---------------------------------------------------------------------------

def test_delete_webhook_204(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.delete(f"/api/webhooks/{w['id']}", headers=h)
    assert r.status_code == 204
    assert client.get(f"/api/webhooks/{w['id']}", headers=h).status_code == 404


def test_delete_webhook_404(client):
    _, h = _setup(client)
    r = client.delete("/api/webhooks/9999", headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/webhooks/{id}/deliveries
# ---------------------------------------------------------------------------

def test_list_deliveries_empty(client):
    _, h = _setup(client)
    w = _create(client, h)
    r = client.get(f"/api/webhooks/{w['id']}/deliveries", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_list_deliveries_after_test_ping(client):
    _, h = _setup(client)
    w = _create(client, h)
    with patch("app.core.webhook._send", return_value=(200, 42, None)):
        client.post(f"/api/webhooks/{w['id']}/test", headers=h)
    r = client.get(f"/api/webhooks/{w['id']}/deliveries", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["event"] == "ping"


def test_list_deliveries_limit(client):
    _, h = _setup(client)
    w = _create(client, h)
    with patch("app.core.webhook._send", return_value=(200, 10, None)):
        for _ in range(5):
            client.post(f"/api/webhooks/{w['id']}/test", headers=h)
    r = client.get(f"/api/webhooks/{w['id']}/deliveries?limit=3", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 3


# ---------------------------------------------------------------------------
# POST /api/webhooks/{id}/test
# ---------------------------------------------------------------------------

def test_test_webhook_success(client):
    _, h = _setup(client)
    w = _create(client, h)
    with patch("app.core.webhook._send", return_value=(200, 55, None)):
        r = client.post(f"/api/webhooks/{w['id']}/test", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["status_code"] == 200
    assert data["duration_ms"] == 55
    assert data["error"] is None


def test_test_webhook_remote_error(client):
    _, h = _setup(client)
    w = _create(client, h)
    with patch("app.core.webhook._send", return_value=(None, 100, "Connection refused")):
        r = client.post(f"/api/webhooks/{w['id']}/test", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["error"] == "Connection refused"


def test_test_webhook_http_error_response(client):
    _, h = _setup(client)
    w = _create(client, h)
    with patch("app.core.webhook._send", return_value=(500, 30, None)):
        r = client.post(f"/api/webhooks/{w['id']}/test", headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["status_code"] == 500


def test_test_webhook_404(client):
    _, h = _setup(client)
    with patch("app.core.webhook._send", return_value=(200, 10, None)):
        r = client.post("/api/webhooks/9999/test", headers=h)
    assert r.status_code == 404
