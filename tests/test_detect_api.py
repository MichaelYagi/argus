"""Happy-path API tests for POST /api/detect/faces|objects|all."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.engine_registry import registry
from app.core.face_engine import FaceDetection
from app.core.object_engine import ObjectDetection
from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app

FAKE_FILE = ("test.jpg", b"fake-image-bytes", "image/jpeg")


def _create_user_and_key(username: str = "tester") -> tuple[int, str]:
    from app.core.security import hash_password
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


def _mock_image(width: int = 640, height: int = 480, fmt: str = "JPEG") -> MagicMock:
    img = MagicMock()
    img.width = width
    img.height = height
    img.format = fmt
    img.crop.return_value.save = MagicMock()
    return img


def _activate_face_model(name: str = "buffalo_l") -> int:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT id FROM models WHERE name = ? AND type = 'face'", (name,)
        ).fetchone()
        model_id = row[0]
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (model_id,))
    return model_id


def _activate_object_model(name: str = "yolov8n") -> int:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT id FROM models WHERE name = ? AND type = 'object'", (name,)
        ).fetchone()
        model_id = row[0]
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (model_id,))
    return model_id


def _insert_source_image(user_id: int, file_path: str = "src.jpg") -> int:
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO source_images (user_id, file_path, width, height) VALUES (?, ?, 640, 480)",
            (user_id, file_path),
        )
        return conn.execute(
            "SELECT id FROM source_images WHERE user_id = ? AND file_path = ?",
            (user_id, file_path),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_detect_faces_requires_api_key(client):
    r = client.post("/api/detect/faces", files={"file": FAKE_FILE})
    assert r.status_code in (401, 403)


def test_detect_faces_rejects_invalid_key(client):
    r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                    headers={"X-API-Key": "argus_bad"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# No active model
# ---------------------------------------------------------------------------

def test_detect_faces_no_active_model_returns_503(client):
    _, key = _create_user_and_key()
    mock_img = _mock_image()
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img):
        r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Happy path — faces
# ---------------------------------------------------------------------------

def test_detect_faces_happy_path(client):
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert data["source_image_id"] == source_id
    assert len(data["faces"]) == 1
    face = data["faces"][0]
    assert face["confidence"] == 0.95
    assert face["bbox"] == {"x": 10, "y": 20, "w": 100, "h": 100}
    assert face["crop_url"] == "/media/crops/crop.jpg"
    assert face["review_status"] == "pending"
    assert face["identity_id"] is None  # no enrolled faces yet


def test_detect_faces_no_detections(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    mock_engine = MagicMock()
    mock_engine.detect.return_value = []
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", 1)), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    assert r.json()["faces"] == []


# ---------------------------------------------------------------------------
# Happy path — objects
# ---------------------------------------------------------------------------

def test_detect_objects_happy_path(client):
    user_id, key = _create_user_and_key()
    _activate_object_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        ObjectDetection(bbox=(5, 10, 200, 150), confidence=0.87, class_name="dog", class_id=16),
    ]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_object_engine", return_value=mock_engine):
        r = client.post("/api/detect/objects", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert len(data["objects"]) == 1
    obj = data["objects"][0]
    assert obj["class_name"] == "dog"
    assert obj["confidence"] == 0.87
    assert obj["identity_id"] is not None  # auto-created from class_name


# ---------------------------------------------------------------------------
# Happy path — all
# ---------------------------------------------------------------------------

def test_detect_all_happy_path(client):
    user_id, key = _create_user_and_key()
    _activate_face_model()
    _activate_object_model()
    source_id = _insert_source_image(user_id)

    face_engine = MagicMock()
    face_engine.detect.return_value = [
        FaceDetection(bbox=(10, 10, 80, 80), confidence=0.9, embedding=MagicMock()),
    ]
    obj_engine = MagicMock()
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 100, 100), confidence=0.8, class_name="cat", class_id=15),
    ]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        r = client.post("/api/detect/all", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert len(data["faces"]) == 1
    assert len(data["objects"]) == 1
