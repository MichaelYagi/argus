"""Tests for GET /api/health and GET /api/capabilities."""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

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


def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_head(client):
    r = client.head("/api/health")
    assert r.status_code == 200


def test_health_no_active_models(client):
    r = client.get("/api/health")
    data = r.json()
    assert data["face_model"] is None
    assert data["object_model"] is None


def test_capabilities_returns_expected_shape(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "detection" in data
    assert "faces" in data["detection"]
    assert "objects" in data["detection"]
    assert "supported_formats" in data
    assert "JPEG" in data["supported_formats"]
    assert data["features"]["change_feed"] is True
    assert data["features"]["environments"] is True


def test_capabilities_no_model_not_available(client):
    r = client.get("/api/capabilities")
    data = r.json()
    assert data["detection"]["faces"]["available"] is False
    assert data["detection"]["objects"]["available"] is False
