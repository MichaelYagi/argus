"""Tests for the in-memory log buffer and the admin /api/logs endpoint."""

from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from app.core import log_buffer
from app.core.security import generate_api_key, hash_api_key, hash_password
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


def _admin(client) -> dict:
    uid = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    key = generate_api_key()
    store.create_api_key(uid, hash_api_key(key), "test")
    return {"X-API-Key": key}


def _non_admin(client) -> dict:
    uid = store.create_user("bob", hash_password("pass12345"), is_admin=False)
    key = generate_api_key()
    store.create_api_key(uid, hash_api_key(key), "test")
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# Ring buffer unit behaviour
# ---------------------------------------------------------------------------

def test_buffer_captures_and_replays():
    log_buffer._handler = None
    log_buffer.install(500)
    logging.getLogger("app.test").info("hello buffer")
    msgs = [e["message"] for e in log_buffer.get_records()]
    assert "hello buffer" in msgs


def test_buffer_resize_preserves_recent():
    log_buffer._handler = None
    log_buffer.install(500)
    for i in range(10):
        logging.getLogger("app.test").info("line %d", i)
    # Handler-level resize does the deque rebuild (module resize() additionally clamps).
    log_buffer._handler.resize(3)
    records = log_buffer.get_records()
    assert len(records) == 3
    assert records[-1]["message"] == "line 9"


def test_buffer_size_is_clamped():
    assert log_buffer.clamp(5) == log_buffer.MIN_SIZE
    assert log_buffer.clamp(10 ** 9) == log_buffer.MAX_SIZE


def test_level_filter():
    log_buffer._handler = None
    log_buffer.install(500)
    logging.getLogger("app.test").info("an info line")
    logging.getLogger("app.test").error("an error line")
    errors = log_buffer.get_records(level="ERROR")
    assert all(e["level"] == "ERROR" for e in errors)
    assert any("an error line" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_logs_endpoint_admin_ok(client):
    h = _admin(client)
    logging.getLogger("app.test").info("api visible line")
    r = client.get("/api/logs", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "buffer_size" in data
    assert isinstance(data["lines"], list)


def test_logs_endpoint_requires_admin(client):
    h = _non_admin(client)
    r = client.get("/api/logs", headers=h)
    assert r.status_code == 403


def test_log_buffer_size_setting_seeded(client):
    _admin(client)
    row = store.get_setting("system.log_buffer_size")
    assert row is not None
    assert row["value"] == "500"


def test_log_buffer_size_bounds_enforced(client):
    h = _admin(client)
    too_small = client.put("/api/settings/system.log_buffer_size", json={"value": 10}, headers=h)
    assert too_small.status_code == 400
    too_big = client.put("/api/settings/system.log_buffer_size", json={"value": 10 ** 9}, headers=h)
    assert too_big.status_code == 400
    ok = client.put("/api/settings/system.log_buffer_size", json={"value": 250}, headers=h)
    assert ok.status_code == 200
