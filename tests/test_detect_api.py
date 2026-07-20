"""Happy-path API tests for POST /api/detect/faces|objects|all."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.inference.face_engine import FaceDetection
from app.inference.object_engine import ObjectDetection
from app.inference.registry import registry
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
    img.mode = "RGB"
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
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
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
         patch("app.api.detect._save_source_image", return_value=("src.jpg", 1, 1.0)), \
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
    mock_engine.has_image_tags = False
    mock_engine.detect.return_value = [
        ObjectDetection(bbox=(5, 10, 200, 150), confidence=0.87, class_name="dog", class_id=16),
    ]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
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
    obj_engine.has_image_tags = False
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 100, 100), confidence=0.8, class_name="cat", class_id=15),
    ]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        r = client.post("/api/detect/all", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert len(data["faces"]) == 1
    assert len(data["objects"]) == 1


# ---------------------------------------------------------------------------
# replace flag — re-detection is idempotent
# ---------------------------------------------------------------------------

def _count_detections(user_id: int, source_id: int, det_type: str) -> int:
    with store._connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM detections WHERE user_id = ? AND source_image_id = ? AND type = ?",
            (user_id, source_id, det_type),
        ).fetchone()[0]


def test_detect_faces_replace_clears_prior(client):
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    mock_img = _mock_image()

    def _detect():
        with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
             patch("app.api.detect.open_and_validate", return_value=mock_img), \
             patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
             patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
             patch("app.api.detect._save_crop", return_value="crop.jpg"), \
             patch.object(registry, "get_face_engine", return_value=mock_engine):
            return client.post("/api/detect/faces?replace=true", files={"file": FAKE_FILE},
                               headers={"X-API-Key": key})

    _detect()
    _detect()
    _detect()
    # Without replace this would be 3; with replace each run wipes the prior.
    assert _count_detections(user_id, source_id, "face") == 1


def test_detect_faces_idempotent_by_default(client):
    """Calling detect/faces twice for the same source returns cached result, no duplicate rows."""
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    mock_img = _mock_image()

    responses = []
    for _ in range(2):
        with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
             patch("app.api.detect.open_and_validate", return_value=mock_img), \
             patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
             patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
             patch("app.api.detect._save_crop", return_value="crop.jpg"), \
             patch.object(registry, "get_face_engine", return_value=mock_engine):
            r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                            headers={"X-API-Key": key})
            responses.append(r.json())

    # Only one detection row stored — second call was idempotent
    assert _count_detections(user_id, source_id, "face") == 1
    # Second response flags that it came from cache
    assert responses[1].get("cached") is True
    assert len(responses[1]["faces"]) == 1


def test_detect_faces_replace_leaves_objects(client):
    """replace on /detect/faces must not touch object detections for the image."""
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)
    # Seed an existing object detection on the same source image
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, NULL, ?, 'object', NULL, 0.8, 0, 0, 50, 50, 'obj.jpg')""",
            (user_id, source_id),
        )

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    mock_img = _mock_image()
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        client.post("/api/detect/faces?replace=true", files={"file": FAKE_FILE},
                    headers={"X-API-Key": key})

    assert _count_detections(user_id, source_id, "face") == 1
    assert _count_detections(user_id, source_id, "object") == 1  # untouched


# ---------------------------------------------------------------------------
# Facial attributes (age / gender / pose)
# ---------------------------------------------------------------------------

def test_detect_faces_includes_and_stores_attributes(client):
    import json
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock(),
                      age=30, gender="M", pose=(1.0, 2.0, 3.0)),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    face = r.json()["faces"][0]
    assert face["age"] == 30
    assert face["gender"] == "M"
    assert face["pose"] == [1.0, 2.0, 3.0]

    det = store.get_detection(face["detection_id"], user_id)
    stored = json.loads(det["attributes"])
    assert stored == {
        "age": 30, "gender": "M", "pose": [1.0, 2.0, 3.0],
        "mask": None, "kps": None, "landmark_2d_106": None, "landmark_3d_68": None,
    }


def test_detect_faces_attributes_default_null(client):
    import json
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/detect/faces", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    face = r.json()["faces"][0]
    assert face["age"] is None and face["gender"] is None and face["pose"] is None
    det = store.get_detection(face["detection_id"], user_id)
    assert json.loads(det["attributes"]) == {
        "age": None, "gender": None, "pose": None,
        "mask": None, "kps": None, "landmark_2d_106": None, "landmark_3d_68": None,
    }


# ---------------------------------------------------------------------------
# 1:1 verify
# ---------------------------------------------------------------------------

def test_verify_happy_path(client):
    _, key = _create_user_and_key()
    _activate_face_model()

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(5, 6, 50, 50), confidence=0.9, embedding=MagicMock(),
                      age=25, gender="F", pose=(0.0, 10.0, 0.0)),
    ]
    with patch("app.api.detect.acquire_image_slot", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/verify",
                        files={"file1": FAKE_FILE, "file2": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert "similarity" in data and "match" in data and "threshold" in data
    assert data["face1"]["bbox"] == {"x": 5, "y": 6, "w": 50, "h": 50}
    assert data["face1"]["gender"] == "F"


def test_verify_no_face_returns_400(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    mock_engine = MagicMock()
    mock_engine.detect.return_value = []  # no faces
    with patch("app.api.detect.acquire_image_slot", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/verify",
                        files={"file1": FAKE_FILE, "file2": FAKE_FILE},
                        headers={"X-API-Key": key})
    assert r.status_code == 400


def test_verify_no_model_returns_503(client):
    _, key = _create_user_and_key()
    with patch("app.api.detect.acquire_image_slot", return_value=b"bytes"):
        r = client.post("/api/verify",
                        files={"file1": FAKE_FILE, "file2": FAKE_FILE},
                        headers={"X-API-Key": key})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# 1:N identify (read-only)
# ---------------------------------------------------------------------------

def test_identify_happy_path_no_enrolled(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(1, 2, 30, 30), confidence=0.88, embedding=MagicMock(),
                      age=40, gender="M", pose=None),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post("/api/identify", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert len(data["faces"]) == 1
    f = data["faces"][0]
    assert f["bbox"] == {"x": 1, "y": 2, "w": 30, "h": 30}
    assert f["identity_id"] is None          # nothing enrolled
    assert f["suggestions"] == []
    assert f["age"] == 40 and f["gender"] == "M"


def test_identify_does_not_store(client):
    user_id, key = _create_user_and_key()
    _activate_face_model()
    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(1, 2, 30, 30), confidence=0.88, embedding=MagicMock()),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        client.post("/api/identify", files={"file": FAKE_FILE}, headers={"X-API-Key": key})

    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM detections WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert n == 0  # read-only — nothing written


# ---------------------------------------------------------------------------
# Stateless test endpoint — POST /api/test
# ---------------------------------------------------------------------------

def test_test_endpoint_happy_path(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    _activate_object_model()

    face_engine = MagicMock()
    face_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock(),
                      age=30, gender="F"),
    ]
    obj_engine = MagicMock()
    obj_engine.has_image_tags = False
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 50, 80), confidence=0.9, class_name="person", class_id=0),
        ObjectDetection(bbox=(5, 5, 40, 40), confidence=0.7, class_name="dog", class_id=16),
    ]

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        r = client.post("/api/test", files={"file": FAKE_FILE}, headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert data["counts"] == {"faces": 1, "objects": 2}
    assert data["available"] == {"faces": True, "objects": True}
    assert data["faces"][0]["age"] == 30
    assert {o["class_name"] for o in data["objects"]} == {"person", "dog"}


def test_test_endpoint_does_not_store(client):
    user_id, key = _create_user_and_key()
    _activate_face_model()
    _activate_object_model()
    face_engine = MagicMock()
    face_engine.detect.return_value = [
        FaceDetection(bbox=(1, 2, 30, 30), confidence=0.8, embedding=MagicMock()),
    ]
    obj_engine = MagicMock()
    obj_engine.has_image_tags = False
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 9, 9), confidence=0.6, class_name="car", class_id=2),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        client.post("/api/test", files={"file": FAKE_FILE}, headers={"X-API-Key": key})

    with store._connect() as conn:
        dets = conn.execute("SELECT COUNT(*) FROM detections WHERE user_id = ?", (user_id,)).fetchone()[0]
        srcs = conn.execute("SELECT COUNT(*) FROM source_images WHERE user_id = ?", (user_id,)).fetchone()[0]
        idents = conn.execute("SELECT COUNT(*) FROM identities WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert dets == 0 and srcs == 0 and idents == 0  # stateless


def test_test_endpoint_requires_api_key(client):
    r = client.post("/api/test", files={"file": FAKE_FILE})
    assert r.status_code in (401, 403)


def test_test_endpoint_type_filter(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    _activate_object_model()
    face_engine = MagicMock()
    face_engine.detect.return_value = [
        FaceDetection(bbox=(1, 2, 30, 30), confidence=0.8, embedding=MagicMock()),
    ]
    obj_engine = MagicMock()
    obj_engine.has_image_tags = False
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 9, 9), confidence=0.6, class_name="car", class_id=2),
    ]
    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        r = client.post("/api/test?type=objects", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})

    assert r.status_code == 200
    data = r.json()
    assert data["counts"]["objects"] == 1
    assert data["faces"] == []
    face_engine.detect.assert_not_called()  # type=objects skips the face engine


def test_test_endpoint_invalid_type(client):
    _, key = _create_user_and_key()
    with patch("app.api.detect.acquire_image", return_value=b"bytes"):
        r = client.post("/api/test?type=bogus", files={"file": FAKE_FILE},
                        headers={"X-API-Key": key})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Stateless batch test — POST /api/test/batch
# ---------------------------------------------------------------------------

def test_test_batch_multipart_files(client):
    _, key = _create_user_and_key()
    _activate_face_model()
    _activate_object_model()

    face_engine = MagicMock()
    face_engine.detect.return_value = [
        FaceDetection(bbox=(10, 20, 100, 100), confidence=0.95, embedding=MagicMock()),
    ]
    obj_engine = MagicMock()
    obj_engine.has_image_tags = False
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 50, 80), confidence=0.9, class_name="person", class_id=0),
    ]

    with patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_face_engine", return_value=face_engine), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        r = client.post(
            "/api/test/batch",
            files=[("file", ("a.jpg", b"x", "image/jpeg")),
                   ("file", ("b.jpg", b"y", "image/jpeg"))],
            data={"type": "all"},
            headers={"X-API-Key": key},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert all(item["counts"]["faces"] == 1 for item in data["results"])
    assert {item["filename"] for item in data["results"]} == {"a.jpg", "b.jpg"}


def test_test_batch_does_not_store(client):
    user_id, key = _create_user_and_key()
    _activate_object_model()
    obj_engine = MagicMock()
    obj_engine.detect.return_value = [
        ObjectDetection(bbox=(0, 0, 9, 9), confidence=0.6, class_name="car", class_id=2),
    ]
    with patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch.object(registry, "get_object_engine", return_value=obj_engine):
        client.post(
            "/api/test/batch?",
            files=[("file", ("a.jpg", b"x", "image/jpeg"))],
            data={"type": "objects"},
            headers={"X-API-Key": key},
        )
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM detections WHERE user_id = ?", (user_id,)).fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM source_images WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert n == 0 and s == 0


def test_test_batch_requires_input(client):
    _, key = _create_user_and_key()
    r = client.post("/api/test/batch", json={"type": "all", "image_urls": []},
                    headers={"X-API-Key": key})
    assert r.status_code == 400


def test_test_batch_requires_api_key(client):
    r = client.post("/api/test/batch", json={"image_urls": ["http://x/y.jpg"]})
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Multi-face label — only the highest-confidence face gets the label
# ---------------------------------------------------------------------------

def _make_embedding():
    import numpy as np
    return np.zeros(512, dtype=np.float32)


def test_detect_faces_label_only_confirms_best_face(client):
    """When label=X is passed with 2 faces, only the highest-confidence face is
    confirmed as X. The other face is stored as unidentified and pending."""
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    low  = FaceDetection(bbox=(10, 10, 80, 80),   confidence=0.70, embedding=_make_embedding())
    high = FaceDetection(bbox=(200, 10, 80, 80),  confidence=0.95, embedding=_make_embedding())

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [low, high]
    mock_img = _mock_image()

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=mock_img), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch("app.api.enroll.enroll_from_detection", return_value=False), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post(
            "/api/detect/faces",
            data={"label": "Alice"},
            files={"file": FAKE_FILE},
            headers={"X-API-Key": key},
        )

    assert r.status_code == 200
    faces = r.json()["faces"]
    assert len(faces) == 2

    confirmed = [f for f in faces if f["review_status"] == "confirmed"]
    pending   = [f for f in faces if f["review_status"] == "pending"]

    assert len(confirmed) == 1
    assert len(pending) == 1
    assert confirmed[0]["confidence"] == 0.95
    assert confirmed[0]["label"] == "Alice"
    assert pending[0]["identity_id"] is None
    assert pending[0]["label"] is None


def test_detect_faces_label_single_face_confirms(client):
    """With a single face and label=X, that face is confirmed as X (unchanged behaviour)."""
    user_id, key = _create_user_and_key()
    _activate_face_model()
    source_id = _insert_source_image(user_id)

    mock_engine = MagicMock()
    mock_engine.detect.return_value = [
        FaceDetection(bbox=(10, 10, 80, 80), confidence=0.90, embedding=_make_embedding()),
    ]

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", source_id, 1.0)), \
         patch("app.api.detect._save_crop", return_value="crop.jpg"), \
         patch("app.api.enroll.enroll_from_detection", return_value=False), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post(
            "/api/detect/faces",
            data={"label": "Bob"},
            files={"file": FAKE_FILE},
            headers={"X-API-Key": key},
        )

    assert r.status_code == 200
    faces = r.json()["faces"]
    assert len(faces) == 1
    assert faces[0]["review_status"] == "confirmed"
    assert faces[0]["label"] == "Bob"


def test_detect_faces_label_no_faces_returns_empty(client):
    """label=X with no detected faces returns an empty list (not an error)."""
    _, key = _create_user_and_key()
    _activate_face_model()

    mock_engine = MagicMock()
    mock_engine.detect.return_value = []

    with patch("app.api.detect.acquire_image", return_value=b"bytes"), \
         patch("app.api.detect.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect.to_rgb_array", return_value=MagicMock()), \
         patch("app.api.detect._save_source_image", return_value=("src.jpg", 1, 1.0)), \
         patch.object(registry, "get_face_engine", return_value=mock_engine):
        r = client.post(
            "/api/detect/faces",
            data={"label": "Carol"},
            files={"file": FAKE_FILE},
            headers={"X-API-Key": key},
        )

    assert r.status_code == 200
    assert r.json()["faces"] == []
