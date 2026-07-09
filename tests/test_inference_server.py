"""Tests for the inference HTTP server (app/inference/server.py)."""

from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import store
from app.inference.registry import registry
from app.inference.server import app


@pytest.fixture()
def client(tmp_path):
    os.environ["DATA_PATH"] = str(tmp_path)
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("DATA_PATH", None)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    r = client.get("/infer/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["face_model"] is None
    assert data["object_model"] is None


def test_health_reflects_loaded_engine(client):
    engine = MagicMock()
    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=engine), \
         patch.object(registry, "get_object_engine", return_value=None):
        r = client.get("/infer/health")
    assert r.status_code == 200
    assert r.json()["face_model"] == "buffalo_l"
    assert r.json()["object_model"] is None


# ---------------------------------------------------------------------------
# POST /infer/faces — 503 when not ready
# ---------------------------------------------------------------------------

def test_infer_faces_no_active_model_returns_503(client):
    r = client.post("/infer/faces", json={"image_b64": "AAAA"})
    assert r.status_code == 503


def test_infer_faces_engine_not_loaded_returns_503(client):
    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=None):
        r = client.post("/infer/faces", json={"image_b64": "AAAA"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /infer/faces — happy path
# ---------------------------------------------------------------------------

def _fake_face(bbox=(10, 20, 80, 90), confidence=0.95):
    emb = MagicMock()
    emb.tobytes.return_value = b"\x00" * 2048  # 512 float32s
    emb.shape = (512,)
    f = MagicMock()
    f.bbox = bbox
    f.confidence = confidence
    f.embedding = emb
    f.age = 30
    f.gender = "M"
    f.pose = None
    f.mask = None
    f.kps = None
    f.landmark_2d_106 = None
    f.landmark_3d_68 = None
    return f


def test_infer_faces_happy_path(client):
    engine = MagicMock()
    engine.detect.return_value = [_fake_face()]
    img_b64 = base64.b64encode(b"fake-image-bytes").decode()

    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=engine), \
         patch("app.inference.server._b64_to_rgb_array", return_value=MagicMock()):
        r = client.post("/infer/faces", json={"image_b64": img_b64})

    assert r.status_code == 200
    data = r.json()
    assert data["model_id"] == 1
    assert data["model_name"] == "buffalo_l"
    assert len(data["faces"]) == 1
    face = data["faces"][0]
    assert face["confidence"] == 0.95
    assert face["bbox"] == [10, 20, 80, 90]
    assert face["embedding"] == base64.b64encode(b"\x00" * 2048).decode()
    assert face["embedding_shape"] == [512]
    assert face["age"] == 30
    assert face["gender"] == "M"
    assert face["pose"] is None


def test_infer_faces_no_detections(client):
    engine = MagicMock()
    engine.detect.return_value = []
    img_b64 = base64.b64encode(b"fake-image-bytes").decode()

    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=engine), \
         patch("app.inference.server._b64_to_rgb_array", return_value=MagicMock()):
        r = client.post("/infer/faces", json={"image_b64": img_b64})

    assert r.status_code == 200
    assert r.json()["faces"] == []


# ---------------------------------------------------------------------------
# POST /infer/objects — 503 when not ready
# ---------------------------------------------------------------------------

def test_infer_objects_no_active_model_returns_503(client):
    r = client.post("/infer/objects", json={"image_b64": "AAAA"})
    assert r.status_code == 503


def test_infer_objects_engine_not_loaded_returns_503(client):
    with patch("app.db.store.get_active_model", return_value={"id": 2, "name": "yolov8n"}), \
         patch.object(registry, "get_object_engine", return_value=None):
        r = client.post("/infer/objects", json={"image_b64": "AAAA"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /infer/objects — happy path
# ---------------------------------------------------------------------------

def _fake_object(class_name="person", confidence=0.88):
    obj = MagicMock()
    obj.bbox = (5, 10, 100, 200)
    obj.confidence = confidence
    obj.class_name = class_name
    obj.class_id = 0
    return obj


def test_infer_objects_happy_path(client):
    engine = MagicMock()
    engine.detect.return_value = [_fake_object()]
    engine.has_image_tags = False
    img_b64 = base64.b64encode(b"fake-image-bytes").decode()

    with patch("app.db.store.get_active_model", return_value={"id": 2, "name": "yolov8n"}), \
         patch.object(registry, "get_object_engine", return_value=engine), \
         patch("app.inference.server._b64_to_rgb_array", return_value=MagicMock()):
        r = client.post("/infer/objects", json={"image_b64": img_b64})

    assert r.status_code == 200
    data = r.json()
    assert data["model_id"] == 2
    assert data["model_name"] == "yolov8n"
    assert data["image_tags"] is None
    assert len(data["objects"]) == 1
    obj = data["objects"][0]
    assert obj["class_name"] == "person"
    assert obj["confidence"] == 0.88
    assert obj["bbox"] == [5, 10, 100, 200]


def test_infer_objects_tagger_engine_returns_image_tags(client):
    engine = MagicMock()
    engine.has_image_tags = True
    engine.detect_with_tags.return_value = (
        ["person", "tree"],
        [_fake_object("person"), _fake_object("tree", 0.72)],
    )
    img_b64 = base64.b64encode(b"fake-image-bytes").decode()

    with patch("app.db.store.get_active_model", return_value={"id": 3, "name": "ram-plus-plus-grounding-dino"}), \
         patch.object(registry, "get_object_engine", return_value=engine), \
         patch("app.inference.server._b64_to_rgb_array", return_value=MagicMock()):
        r = client.post("/infer/objects", json={"image_b64": img_b64})

    assert r.status_code == 200
    data = r.json()
    assert data["image_tags"] == ["person", "tree"]
    assert len(data["objects"]) == 2
