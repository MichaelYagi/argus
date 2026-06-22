"""Happy-path tests for POST /api/faces/enroll and POST /api/identities/{id}/enroll."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.engine_registry import registry
from app.core.face_engine import FaceDetection
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


def _mock_engine_with_face() -> MagicMock:
    engine = MagicMock()
    engine.detect.return_value = [
        FaceDetection(bbox=(10, 10, 80, 80), confidence=0.95, embedding=MagicMock()),
    ]
    return engine


def _mock_img() -> MagicMock:
    img = MagicMock()
    img.width = 640
    img.height = 480
    img.format = "JPEG"
    return img


# ---------------------------------------------------------------------------
# POST /api/faces/enroll — create identity + first embedding
# ---------------------------------------------------------------------------

def test_enroll_new_creates_identity(client):
    _, h = _setup(client)
    engine = _mock_engine_with_face()
    img = _mock_img()

    with patch("app.api.enroll.open_and_validate", return_value=img), \
         patch("app.api.enroll.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.enroll._save_source", return_value="src.jpg"), \
         patch("app.api.enroll._to_bytes", return_value=b"\x00" * 2048), \
         patch.object(registry, "get_face_engine", return_value=engine):
        r = client.post(
            "/api/faces/enroll",
            data={"name": "Mike"},
            files={"file": ("face.jpg", b"fake", "image/jpeg")},
            headers=h,
        )

    assert r.status_code == 201
    data = r.json()
    assert data["label"] == "Mike"
    assert data["embeddings"] == 1

    identities = client.get("/api/identities", headers=h).json()
    assert len(identities) == 1
    assert identities[0]["label"] == "Mike"


def test_enroll_new_no_face_returns_400(client):
    _, h = _setup(client)
    engine = MagicMock()
    engine.detect.return_value = []
    img = _mock_img()

    with patch("app.api.enroll.open_and_validate", return_value=img), \
         patch("app.api.enroll.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.enroll._save_source", return_value="src.jpg"), \
         patch.object(registry, "get_face_engine", return_value=engine):
        r = client.post(
            "/api/faces/enroll",
            data={"name": "Mike"},
            files={"file": ("face.jpg", b"fake", "image/jpeg")},
            headers=h,
        )

    assert r.status_code == 400


def test_enroll_new_duplicate_name_returns_409(client):
    _, h = _setup(client)
    engine = _mock_engine_with_face()
    img = _mock_img()

    patches = [
        patch("app.api.enroll.open_and_validate", return_value=img),
        patch("app.api.enroll.to_rgb_array", return_value=MagicMock()),
        patch("app.api.enroll._save_source", return_value="src.jpg"),
        patch("app.api.enroll._to_bytes", return_value=b"\x00" * 2048),
        patch.object(registry, "get_face_engine", return_value=engine),
    ]
    for p in patches:
        p.start()

    client.post("/api/faces/enroll", data={"name": "Mike"},
                files={"file": ("f.jpg", b"x", "image/jpeg")}, headers=h)
    r = client.post("/api/faces/enroll", data={"name": "Mike"},
                    files={"file": ("f.jpg", b"x", "image/jpeg")}, headers=h)

    for p in patches:
        p.stop()

    assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/identities/{id}/enroll — add embedding to existing identity
# ---------------------------------------------------------------------------

def test_enroll_existing_adds_embedding(client):
    _, h = _setup(client)
    created = client.post("/api/identities", json={"label": "Mike", "type": "face"}, headers=h).json()
    identity_id = created["id"]

    engine = _mock_engine_with_face()
    img = _mock_img()

    with patch("app.api.enroll.open_and_validate", return_value=img), \
         patch("app.api.enroll.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.enroll._save_source", return_value="src.jpg"), \
         patch("app.api.enroll._to_bytes", return_value=b"\x00" * 2048), \
         patch.object(registry, "get_face_engine", return_value=engine):
        r = client.post(
            f"/api/identities/{identity_id}/enroll",
            files={"file": ("face.jpg", b"fake", "image/jpeg")},
            headers=h,
        )

    assert r.status_code == 201
    data = r.json()
    assert data["identity_id"] == identity_id
    assert "embedding_id" in data

    detail = client.get(f"/api/identities/{identity_id}", headers=h).json()
    assert detail["embedding_count"] == 1


def test_enroll_existing_not_found(client):
    _, h = _setup(client)
    r = client.post(
        "/api/identities/999/enroll",
        files={"file": ("f.jpg", b"x", "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reference toggle — POST/DELETE /api/detections/{id}/enroll + gallery flag
# ---------------------------------------------------------------------------

def _insert_face_detection(user_id: int, identity_id: int, crop: str) -> int:
    src_id = store.get_or_create_source_image(user_id, f"src-{crop}", 640, 480)
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path, embedding, review_status)
               VALUES (?, ?, ?, 'face', NULL, 0.9, 0, 0, 80, 80, ?, ?, 'confirmed')""",
            (user_id, identity_id, src_id, crop, b"\x00" * 2048),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_reference_toggle_add_and_remove(client):
    user_id, h = _setup(client)
    identity_id = store.create_identity(user_id, "face", "Noah")
    det_id = _insert_face_detection(user_id, identity_id, "crop1.jpg")

    # Gallery: not a reference yet
    g = client.get(f"/api/identities/{identity_id}/gallery", headers=h).json()
    assert g["items"][0]["enrolled"] is False

    # Add to references
    r = client.post(f"/api/detections/{det_id}/enroll", headers=h)
    assert r.status_code == 201
    assert r.json()["enrolled"] is True
    g = client.get(f"/api/identities/{identity_id}/gallery", headers=h).json()
    assert g["items"][0]["enrolled"] is True

    # Remove from references
    r = client.delete(f"/api/detections/{det_id}/enroll", headers=h)
    assert r.status_code == 200
    assert r.json()["removed"] is True
    assert r.json()["enrolled"] is False
    g = client.get(f"/api/identities/{identity_id}/gallery", headers=h).json()
    assert g["items"][0]["enrolled"] is False


def test_reference_remove_when_not_enrolled_is_noop(client):
    user_id, h = _setup(client)
    identity_id = store.create_identity(user_id, "face", "Noah")
    det_id = _insert_face_detection(user_id, identity_id, "crop1.jpg")

    r = client.delete(f"/api/detections/{det_id}/enroll", headers=h)
    assert r.status_code == 200
    assert r.json()["removed"] is False


def test_delete_detection_removes_its_reference(client):
    user_id, h = _setup(client)
    identity_id = store.create_identity(user_id, "face", "Noah")
    det_id = _insert_face_detection(user_id, identity_id, "crop1.jpg")

    client.post(f"/api/detections/{det_id}/enroll", headers=h)
    assert client.get(f"/api/identities/{identity_id}", headers=h).json()["embedding_count"] == 1

    # Deleting the detection must also drop its reference (no orphan).
    r = client.delete(f"/api/detections/{det_id}", headers=h)
    assert r.status_code == 204
    assert client.get(f"/api/identities/{identity_id}", headers=h).json()["embedding_count"] == 0


def test_startup_reconciles_orphaned_references(client):
    user_id, h = _setup(client)
    identity_id = store.create_identity(user_id, "face", "Noah")
    det_id = _insert_face_detection(user_id, identity_id, "crop1.jpg")
    client.post(f"/api/detections/{det_id}/enroll", headers=h)
    assert client.get(f"/api/identities/{identity_id}", headers=h).json()["embedding_count"] == 1

    # Simulate an orphan from an older build: drop the detection row directly,
    # leaving the reference behind.
    with store._connect() as conn:
        conn.execute("DELETE FROM detections WHERE id = ?", (det_id,))
    assert client.get(f"/api/identities/{identity_id}", headers=h).json()["embedding_count"] == 1

    # Startup migration removes the orphaned reference.
    store.init_db()
    assert client.get(f"/api/identities/{identity_id}", headers=h).json()["embedding_count"] == 0
