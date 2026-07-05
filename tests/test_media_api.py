"""Tests for GET /media/crops/* and GET /media/sources/*."""

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


def test_serve_crop_404(client):
    r = client.get("/media/crops/nonexistent.jpg")
    assert r.status_code == 404


def test_serve_source_404(client):
    r = client.get("/media/sources/nonexistent.jpg")
    assert r.status_code == 404


def test_serve_crop_returns_file(client, tmp_path):
    crops = tmp_path / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    (crops / "test.jpg").write_bytes(b"fake-image-data")
    r = client.get("/media/crops/test.jpg")
    assert r.status_code == 200
    assert r.content == b"fake-image-data"


def test_serve_source_returns_file(client, tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "photo.jpg").write_bytes(b"source-image-data")
    r = client.get("/media/sources/photo.jpg")
    assert r.status_code == 200
    assert r.content == b"source-image-data"
