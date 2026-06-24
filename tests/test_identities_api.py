"""Happy-path tests for identity CRUD, gallery, and unknown detections."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app

FAKE_FILE = ("test.jpg", b"fake", "image/jpeg")


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["DATA_PATH"] = str(tmp_path)
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)
    os.environ.pop("DATA_PATH", None)


def _setup(client) -> tuple[int, dict]:
    """Create a user + API key. Returns (user_id, headers)."""
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("password123"))
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return user_id, {"X-API-Key": key}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_list_identities_empty(client):
    _, h = _setup(client)
    r = client.get("/api/identities", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_create_and_list_identity(client):
    _, h = _setup(client)
    r = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h)
    assert r.status_code == 201
    data = r.json()
    assert data["label"] == "Mike"
    assert data["type"] == "face"

    r2 = client.get("/api/identities", headers=h)
    assert len(r2.json()) == 1


def test_create_duplicate_returns_409(client):
    _, h = _setup(client)
    client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h)
    r = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h)
    assert r.status_code == 409


def test_list_filter_by_type(client):
    _, h = _setup(client)
    client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h)
    client.post("/api/identities", json={"label": "dog", "type": "object"}, headers=h)

    r = client.get("/api/identities?type=face", headers=h)
    assert len(r.json()) == 1
    assert r.json()[0]["label"] == "Mike"


def test_list_search(client):
    _, h = _setup(client)
    client.post("/api/identities", json={"label": "Michael", "type": "face"}, headers=h)
    client.post("/api/identities", json={"label": "Sarah", "type": "face"}, headers=h)

    r = client.get("/api/identities?q=mich", headers=h)
    assert len(r.json()) == 1
    assert r.json()[0]["label"] == "Michael"


def test_get_identity_detail(client):
    _, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    r = client.get(f"/api/identities/{created['id']}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "Mike"
    assert data["detection_count"] == 0
    assert data["embedding_count"] == 0
    assert data["thumbnail_url"] is None


def test_get_identity_not_found(client):
    _, h = _setup(client)
    r = client.get("/api/identities/999", headers=h)
    assert r.status_code == 404


def test_delete_identity(client):
    _, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    r = client.delete(f"/api/identities/{created['id']}", headers=h)
    assert r.status_code == 204

    r2 = client.get("/api/identities", headers=h)
    assert r2.json() == []


def test_delete_identity_not_found(client):
    _, h = _setup(client)
    r = client.delete("/api/identities/999", headers=h)
    assert r.status_code == 404


def test_users_cannot_see_each_others_identities(client):
    from app.core.security import hash_password
    user2 = store.create_user("bob", hash_password("password123"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")
    h2 = {"X-API-Key": k2}

    _, h1 = _setup(client)
    client.post("/api/identities", json={"label": "Alice identity", "type": "face"}, headers=h1)

    r = client.get("/api/identities", headers=h2)
    assert r.json() == []


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------

def _insert_detection(user_id: int, identity_id: int, crop: str = "crop.jpg", offset: int = 0) -> int:
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO source_images (user_id, environment_id, file_path, width, height)
               VALUES (?, ?, ?, 640, 480)""",
            (user_id, env_id, f"src_{offset}.jpg"),
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path,
                detected_at)
               VALUES (?, ?, ?, ?, 'face', NULL, 0.9, 0, 0, 100, 100, ?,
                datetime('now', ? || ' seconds'))""",
            (user_id, env_id, identity_id, src_id, crop, str(-offset)),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_gallery_empty(client):
    user_id, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    r = client.get(f"/api/identities/{created['id']}/gallery", headers=h)
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None, "has_more": False}


def test_gallery_returns_items(client):
    user_id, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    identity_id = created["id"]
    _insert_detection(user_id, identity_id, "a.jpg", offset=0)
    _insert_detection(user_id, identity_id, "b.jpg", offset=1)

    r = client.get(f"/api/identities/{identity_id}/gallery", headers=h)
    data = r.json()
    assert len(data["items"]) == 2
    assert data["has_more"] is False


def test_gallery_pagination(client):
    user_id, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    identity_id = created["id"]
    for i in range(5):
        _insert_detection(user_id, identity_id, f"crop_{i}.jpg", offset=i)

    r = client.get(f"/api/identities/{identity_id}/gallery?limit=3", headers=h)
    data = r.json()
    assert len(data["items"]) == 3
    assert data["has_more"] is True
    assert data["next_cursor"] is not None

    r2 = client.get(
        f"/api/identities/{identity_id}/gallery?limit=3&cursor={data['next_cursor']}",
        headers=h,
    )
    data2 = r2.json()
    assert len(data2["items"]) == 2
    assert data2["has_more"] is False


# ---------------------------------------------------------------------------
# Unknown detections
# ---------------------------------------------------------------------------

def _insert_unknown(user_id: int, det_type: str = "face", offset: int = 0) -> None:
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO source_images (user_id, environment_id, file_path, width, height) VALUES (?, ?, ?, 640, 480)",
            (user_id, env_id, f"unknown_src_{offset}.jpg"),
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path, detected_at)
               VALUES (?, ?, NULL, ?, ?, NULL, 0.6, 0, 0, 100, 100, 'unk.jpg',
                datetime('now', ? || ' seconds'))""",
            (user_id, env_id, src_id, det_type, str(-offset)),
        )


def test_unknown_detections_empty(client):
    _, h = _setup(client)
    r = client.get("/api/detections/unknown", headers=h)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_unknown_detections_returns_items(client):
    user_id, h = _setup(client)
    _insert_unknown(user_id, "face", 0)
    _insert_unknown(user_id, "object", 1)

    r = client.get("/api/detections/unknown", headers=h)
    assert len(r.json()["items"]) == 2


def test_unknown_detections_filter_by_type(client):
    user_id, h = _setup(client)
    _insert_unknown(user_id, "face", 0)
    _insert_unknown(user_id, "object", 1)

    r = client.get("/api/detections/unknown?type=face", headers=h)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "face"
