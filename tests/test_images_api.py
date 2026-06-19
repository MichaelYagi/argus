"""Tests for GET /api/images/{id}/faces and POST /api/images/{id}/tag."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


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
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("pass12345"))
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return user_id, {"X-API-Key": key}


def _insert_source(user_id: int, filename: str = "photo.jpg") -> int:
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO source_images (user_id, file_path, width, height) VALUES (?, ?, 1920, 1080)",
            (user_id, filename),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_face(user_id: int, src_id: int, label: str | None = None,
                 bbox: tuple = (10, 20, 100, 100), conf: float = 0.9) -> int:
    identity_id = None
    if label:
        identity_id = store.get_or_create_identity(user_id, "face", label)
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, ?, 'face', NULL, ?, ?, ?, ?, ?, 'crop.jpg')""",
            (user_id, identity_id, src_id, conf, *bbox),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# GET /api/images/{id}/faces
# ---------------------------------------------------------------------------

def test_image_faces_not_found(client):
    _, h = _setup(client)
    r = client.get("/api/images/999/faces", headers=h)
    assert r.status_code == 404


def test_image_faces_empty(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    assert r.status_code == 200
    assert r.json()["faces"] == []
    assert r.json()["width"] == 1920
    assert r.json()["height"] == 1080


def test_image_faces_returns_detections(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id, label="Noah", bbox=(10, 20, 100, 100))
    d2 = _insert_face(user_id, src_id, bbox=(200, 50, 80, 80))

    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    data = r.json()
    assert len(data["faces"]) == 2

    face_ids = {f["detection_id"] for f in data["faces"]}
    assert {d1, d2} == face_ids

    labeled = next(f for f in data["faces"] if f["detection_id"] == d1)
    assert labeled["label"] == "Noah"
    assert labeled["bbox"] == {"x": 10, "y": 20, "w": 100, "h": 100}

    unlabeled = next(f for f in data["faces"] if f["detection_id"] == d2)
    assert unlabeled["identity_id"] is None


def test_image_faces_excludes_objects(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    _insert_face(user_id, src_id)  # face
    with store._connect() as conn:  # object
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, NULL, ?, 'object', NULL, 0.8, 0, 0, 50, 50, 'obj.jpg')""",
            (user_id, src_id),
        )
    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    assert len(r.json()["faces"]) == 1


def test_image_faces_not_accessible_by_other_user(client):
    from app.core.security import hash_password
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)
    _insert_face(user_id, src_id)

    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")
    h2 = {"X-API-Key": k2}

    r = client.get(f"/api/images/{src_id}/faces", headers=h2)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/images/{id}/tag
# ---------------------------------------------------------------------------

def test_tag_labels_faces(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id)
    d2 = _insert_face(user_id, src_id)
    iid = store.get_or_create_identity(user_id, "face", "Noah")

    r = client.post(f"/api/images/{src_id}/tag", json=[
        {"detection_id": d1, "identity_id": iid},
        {"detection_id": d2, "label": "Sarah"},
    ], headers=h)

    assert r.status_code == 200
    results = {item["detection_id"]: item for item in r.json()}
    assert results[d1]["label"] == "Noah"
    assert results[d2]["label"] == "Sarah"
    assert results[d1]["status"] == "labeled"


def test_tag_creates_identity_from_label(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id)

    client.post(f"/api/images/{src_id}/tag", json=[
        {"detection_id": d1, "label": "BrandNew"},
    ], headers=h)

    identities = client.get("/api/identities", headers=h).json()
    assert any(i["label"] == "BrandNew" for i in identities)


def test_tag_wrong_image_detection_returns_not_found(client):
    user_id, h = _setup(client)
    src1 = _insert_source(user_id, "photo1.jpg")
    src2 = _insert_source(user_id, "photo2.jpg")
    d_on_src2 = _insert_face(user_id, src2)

    r = client.post(f"/api/images/{src1}/tag", json=[
        {"detection_id": d_on_src2, "label": "X"},
    ], headers=h)

    assert r.json()[0]["status"] == "not_found"
