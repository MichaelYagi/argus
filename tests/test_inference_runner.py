"""Tests for app/inference/runner.py — in-process and remote dispatch."""

from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from app.inference.face_engine import FaceDetection
from app.inference.object_engine import ObjectDetection
from app.inference.registry import registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _img_array():
    """Minimal mock numpy array with the attributes _encode_array needs."""
    arr = MagicMock()
    arr.tobytes.return_value = b"\x01" * (4 * 4 * 3)
    arr.shape = [4, 4, 3]
    return arr


def _face_response(model_id=1, model_name="buffalo_l", n_faces=1):
    emb_b64 = base64.b64encode(b"\x00" * 8).decode()
    faces = [
        {
            "bbox": [10, 20, 80, 90],
            "confidence": 0.95,
            "embedding": emb_b64,
            "embedding_shape": [2],
            "age": 30,
            "gender": "M",
            "pose": None,
            "mask": None,
            "kps": None,
            "landmark_2d_106": None,
            "landmark_3d_68": None,
        }
    ] * n_faces
    return {"model_id": model_id, "model_name": model_name, "faces": faces}


def _object_response(model_id=2, model_name="yolov8n", image_tags=None):
    return {
        "model_id": model_id,
        "model_name": model_name,
        "image_tags": image_tags,
        "objects": [
            {"bbox": [5, 10, 100, 200], "confidence": 0.88, "class_name": "person", "class_id": 0}
        ],
    }


@pytest.fixture(autouse=True)
def clear_inference_url():
    """Ensure INFERENCE_URL is unset between tests."""
    os.environ.pop("INFERENCE_URL", None)
    yield
    os.environ.pop("INFERENCE_URL", None)


# ---------------------------------------------------------------------------
# In-process path (INFERENCE_URL unset)
# ---------------------------------------------------------------------------

def test_infer_faces_in_process_calls_engine():
    from app.inference.runner import infer_faces

    engine = MagicMock()
    engine.detect.return_value = []
    img = _img_array()

    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=engine):
        faces, model_row = infer_faces(img)

    engine.detect.assert_called_once_with(img)
    assert faces == []
    assert model_row["id"] == 1


def test_infer_objects_in_process_calls_engine():
    from app.inference.runner import infer_objects

    engine = MagicMock()
    engine.detect.return_value = []
    engine.has_image_tags = False
    img = _img_array()

    with patch("app.db.store.get_active_model", return_value={"id": 2, "name": "yolov8n"}), \
         patch.object(registry, "get_object_engine", return_value=engine):
        objects, image_tags, model_row = infer_objects(img)

    engine.detect.assert_called_once_with(img)
    assert objects == []
    assert image_tags is None
    assert model_row["id"] == 2


def test_infer_faces_in_process_503_no_model():
    from fastapi import HTTPException

    from app.inference.runner import infer_faces

    with patch("app.db.store.get_active_model", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            infer_faces(_img_array())
    assert exc_info.value.status_code == 503


def test_infer_faces_in_process_503_engine_not_loaded():
    from fastapi import HTTPException

    from app.inference.runner import infer_faces

    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            infer_faces(_img_array())
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Remote path (INFERENCE_URL set)
# ---------------------------------------------------------------------------

def test_infer_faces_remote_dispatches_when_url_set():
    from app.inference.runner import infer_faces

    os.environ["INFERENCE_URL"] = "http://inference:8200"

    with patch("app.inference.runner._remote_post", return_value=_face_response()) as mock_post:
        faces, model_row = infer_faces(_img_array())

    mock_post.assert_called_once()
    call_url, call_path, call_payload = mock_post.call_args.args
    assert call_url == "http://inference:8200"
    assert call_path == "/infer/faces"
    assert "array_b64" in call_payload
    assert call_payload["array_shape"] == [4, 4, 3]

    assert len(faces) == 1
    assert isinstance(faces[0], FaceDetection)
    assert faces[0].confidence == 0.95
    assert faces[0].bbox == (10, 20, 80, 90)
    assert faces[0].age == 30
    assert faces[0].gender == "M"
    assert faces[0].pose is None
    assert model_row == {"id": 1, "name": "buffalo_l"}


def test_infer_objects_remote_dispatches_when_url_set():
    from app.inference.runner import infer_objects

    os.environ["INFERENCE_URL"] = "http://inference:8200"

    with patch("app.inference.runner._remote_post", return_value=_object_response()) as mock_post:
        objects, image_tags, model_row = infer_objects(_img_array())

    mock_post.assert_called_once()
    _, call_path, _ = mock_post.call_args.args
    assert call_path == "/infer/objects"

    assert len(objects) == 1
    assert isinstance(objects[0], ObjectDetection)
    assert objects[0].class_name == "person"
    assert objects[0].confidence == 0.88
    assert image_tags is None
    assert model_row == {"id": 2, "name": "yolov8n"}


def test_infer_objects_remote_returns_image_tags():
    from app.inference.runner import infer_objects

    os.environ["INFERENCE_URL"] = "http://inference:8200"
    resp = _object_response(image_tags=["person", "car"])

    with patch("app.inference.runner._remote_post", return_value=resp):
        objects, image_tags, _ = infer_objects(_img_array())

    assert image_tags == ["person", "car"]


def test_remote_path_not_used_when_url_unset():
    """Verify _remote_post is never called when INFERENCE_URL is absent."""
    from app.inference.runner import infer_faces

    engine = MagicMock()
    engine.detect.return_value = []

    with patch("app.db.store.get_active_model", return_value={"id": 1, "name": "buffalo_l"}), \
         patch.object(registry, "get_face_engine", return_value=engine), \
         patch("app.inference.runner._remote_post") as mock_post:
        infer_faces(_img_array())

    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Remote path — error propagation
# ---------------------------------------------------------------------------

def test_remote_503_propagated():
    from fastapi import HTTPException

    from app.inference.runner import infer_faces

    os.environ["INFERENCE_URL"] = "http://inference:8200"

    with patch("app.inference.runner._remote_post", side_effect=HTTPException(503, "No active face model.")):
        with pytest.raises(HTTPException) as exc_info:
            infer_faces(_img_array())
    assert exc_info.value.status_code == 503


def test_remote_connection_error_raises_503():
    from fastapi import HTTPException

    from app.inference.runner import _remote_post

    class _ConnErr(Exception):
        pass

    # httpx is mocked in conftest — wire up a real exception class so the
    # try/except in _remote_post actually fires.
    with patch("httpx.post", side_effect=_ConnErr("refused")), \
         patch("httpx.RequestError", _ConnErr):
        with pytest.raises(HTTPException) as exc_info:
            _remote_post("http://inference:8200", "/infer/faces", {})
    assert exc_info.value.status_code == 503
    assert "unreachable" in exc_info.value.detail


def test_remote_503_response_becomes_503_exception():
    from fastapi import HTTPException

    from app.inference.runner import _remote_post

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"detail": "Face engine not loaded."}

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc_info:
            _remote_post("http://inference:8200", "/infer/faces", {})
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Face engine not loaded."


# ---------------------------------------------------------------------------
# Embedding round-trip
# ---------------------------------------------------------------------------

def test_remote_face_embedding_deserialized():
    """Embedding bytes arrive as base64, get decoded back to numpy."""
    from app.inference.runner import infer_faces

    os.environ["INFERENCE_URL"] = "http://inference:8200"

    emb_bytes = b"\x3f\x80\x00\x00" * 4  # four float32 1.0s
    resp = {
        "model_id": 1,
        "model_name": "buffalo_l",
        "faces": [{
            "bbox": [0, 0, 10, 10],
            "confidence": 0.9,
            "embedding": base64.b64encode(emb_bytes).decode(),
            "embedding_shape": [4],
            "age": None, "gender": None, "pose": None,
            "mask": None, "kps": None,
            "landmark_2d_106": None, "landmark_3d_68": None,
        }],
    }

    with patch("app.inference.runner._remote_post", return_value=resp):
        faces, _ = infer_faces(_img_array())

    assert len(faces) == 1
    face = faces[0]
    assert face.embedding is not None
