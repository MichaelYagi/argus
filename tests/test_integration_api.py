"""Tests for client-integration features: external_ref, change feed,
capabilities, batch label, batch read."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key, hash_password
from app.db import store
from app.main import app


def _create_user_and_key(username: str = "tester") -> tuple[int, str]:
    user_id = store.create_user(username, hash_password("password123"))
    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), "test key")
    return user_id, plaintext


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


def _h(key: str) -> dict:
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# external_ref
# ---------------------------------------------------------------------------

def test_create_identity_with_external_ref_and_lookup(client):
    _, key = _create_user_and_key()
    r = client.post("/api/identities",
                    json={"label": "Noah", "type": "face", "external_ref": "shashin-4826"},
                    headers=_h(key))
    assert r.status_code == 201
    assert r.json()["external_ref"] == "shashin-4826"

    # Lookup by ref
    r = client.get("/api/identities?external_ref=shashin-4826", headers=_h(key))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1 and items[0]["label"] == "Noah"
    assert items[0]["external_ref"] == "shashin-4826"


def test_set_external_ref_endpoint(client):
    _, key = _create_user_and_key()
    iid = client.post("/api/identities", json={"label": "Mia", "type": "face"},
                      headers=_h(key)).json()["id"]
    r = client.put(f"/api/identities/{iid}/external_ref",
                   json={"external_ref": "person-99"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["external_ref"] == "person-99"

    got = client.get("/api/identities?external_ref=person-99", headers=_h(key)).json()
    assert len(got) == 1 and got[0]["id"] == iid


def test_set_external_ref_unknown_identity_404(client):
    _, key = _create_user_and_key()
    r = client.put("/api/identities/9999/external_ref",
                   json={"external_ref": "x"}, headers=_h(key))
    assert r.status_code == 404


def test_source_image_external_ref_lookup(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.get_or_create_source_image(user_id, "abc.jpg", 100, 100, env_id, "img-ref-1")
    r = client.get("/api/images?external_ref=img-ref-1", headers=_h(key))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["external_ref"] == "img-ref-1"


# ---------------------------------------------------------------------------
# Change feed
# ---------------------------------------------------------------------------

def test_change_feed_records_identity_events(client):
    _, key = _create_user_and_key()
    # Empty initially
    r = client.get("/api/changes", headers=_h(key))
    assert r.status_code == 200 and r.json()["changes"] == []

    iid = client.post("/api/identities", json={"label": "Ana", "type": "face", "external_ref": "a1"},
                      headers=_h(key)).json()["id"]

    data = client.get("/api/changes", headers=_h(key)).json()
    assert len(data["changes"]) == 1
    ev = data["changes"][0]
    assert ev["entity_type"] == "identity" and ev["action"] == "created"
    assert ev["entity_id"] == iid and ev["external_ref"] == "a1"

    cursor = data["next_cursor"]
    # Rename → relabeled event after the cursor
    client.put(f"/api/identities/{iid}", json={"label": "Ana B"}, headers=_h(key))
    data2 = client.get(f"/api/changes?since={cursor}", headers=_h(key)).json()
    assert len(data2["changes"]) == 1
    assert data2["changes"][0]["action"] == "relabeled"


def test_change_feed_pagination(client):
    _, key = _create_user_and_key()
    for i in range(3):
        client.post("/api/identities", json={"label": f"P{i}", "type": "object"}, headers=_h(key))
    data = client.get("/api/changes?limit=2", headers=_h(key)).json()
    assert len(data["changes"]) == 2 and data["has_more"] is True
    rest = client.get(f"/api/changes?since={data['next_cursor']}&limit=2", headers=_h(key)).json()
    assert len(rest["changes"]) == 1 and rest["has_more"] is False


def test_change_feed_requires_api_key(client):
    r = client.get("/api/changes")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_capabilities_endpoint(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert "detection" in data and "faces" in data["detection"]
    assert data["features"]["external_ref"] is True
    assert data["features"]["change_feed"] is True
    assert "JPEG" in data["supported_formats"]
    assert data["image_input"] == ["file", "image_url", "image_base64"]


# ---------------------------------------------------------------------------
# Batch label + batch read
# ---------------------------------------------------------------------------

def _insert_object_detection(user_id: int, label: str) -> int:
    """Create an object identity + a source image + a detection directly."""
    env_id = store.get_default_environment_id(user_id)
    identity_id = store.get_or_create_identity(user_id, "object", label, env_id)
    source_id = store.get_or_create_source_image(user_id, f"{label}.jpg", 100, 100, env_id)
    return store.insert_detection(
        user_id=user_id, identity_id=identity_id, source_image_id=source_id,
        detection_type="object", model_id=None, confidence=0.9,
        bbox_x=1, bbox_y=2, bbox_w=3, bbox_h=4, crop_path=f"{label}.jpg",
        environment_id=env_id,
    )


def test_batch_label(client):
    user_id, key = _create_user_and_key()
    d1 = _insert_object_detection(user_id, "bench")
    d2 = _insert_object_detection(user_id, "chair")

    r = client.post("/api/detections/label", json={"items": [
        {"detection_id": d1, "label": "park bench"},
        {"detection_id": d2, "label": "office chair"},
        {"detection_id": 99999, "label": "ghost"},
    ]}, headers=_h(key))
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["ok"] is True and results[0]["label"] == "park bench"
    assert results[1]["ok"] is True
    assert results[2]["ok"] is False and "not found" in results[2]["error"].lower()


def test_batch_read(client):
    user_id, key = _create_user_and_key()
    d1 = _insert_object_detection(user_id, "dog")
    d2 = _insert_object_detection(user_id, "cat")

    r = client.post("/api/detections/query",
                    json={"detection_ids": [d1, d2, 99999]}, headers=_h(key))
    assert r.status_code == 200
    items = r.json()["items"]
    # Unknown id simply absent
    assert {i["detection_id"] for i in items} == {d1, d2}
    labels = {i["label"] for i in items}
    assert labels == {"dog", "cat"}


def test_batch_label_empty_400(client):
    _, key = _create_user_and_key()
    r = client.post("/api/detections/label", json={"items": []}, headers=_h(key))
    assert r.status_code == 400
