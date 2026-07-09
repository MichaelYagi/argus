from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core import settings_cache
from app.inference.face_engine import FaceDetection, FaceEngine, _face_ctx_id


def _fake_face(x1: float, y1: float, x2: float, y2: float, score: float = 0.95) -> MagicMock:
    face = MagicMock()
    face.det_score = score
    face.bbox = [x1, y1, x2, y2]
    face.embedding = MagicMock()
    face.embedding.astype.return_value = MagicMock()
    return face


def _make_engine(tmp_path: Path) -> FaceEngine:
    with patch("insightface.app.FaceAnalysis") as mock_cls:
        mock_cls.return_value = MagicMock()
        engine = FaceEngine("buffalo_l", tmp_path)
    return engine


# ---------------------------------------------------------------------------
# GPU selection
# ---------------------------------------------------------------------------

def test_ctx_id_cpu_when_use_gpu_false():
    settings_cache.cache.set("system.use_gpu", "false", "bool")
    assert _face_ctx_id() == -1


def test_ctx_id_gpu_when_provider_available():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("onnxruntime.get_available_providers", return_value=["CUDAExecutionProvider"]):
        assert _face_ctx_id() == 0


def test_ctx_id_cpu_when_provider_unavailable():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]):
        assert _face_ctx_id() == -1


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detect_returns_face_detections(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("face.detection_confidence", "0.5", "float")
    settings_cache.cache.set("face.min_face_size", "10", "int")

    engine._app.get.return_value = [_fake_face(10, 20, 110, 120, score=0.95)]
    result = engine.detect(MagicMock())

    assert len(result) == 1
    d = result[0]
    assert isinstance(d, FaceDetection)
    assert d.bbox == (10, 20, 100, 100)
    assert d.confidence == 0.95


def test_detect_bbox_conversion(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("face.detection_confidence", "0.0", "float")
    settings_cache.cache.set("face.min_face_size", "0", "int")

    engine._app.get.return_value = [_fake_face(5, 15, 55, 115)]
    result = engine.detect(MagicMock())

    assert result[0].bbox == (5, 15, 50, 100)  # (x1, y1, x2-x1, y2-y1)


def test_detect_filters_low_confidence(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("face.detection_confidence", "0.8", "float")
    settings_cache.cache.set("face.min_face_size", "0", "int")

    engine._app.get.return_value = [
        _fake_face(0, 0, 100, 100, score=0.9),   # passes
        _fake_face(0, 0, 100, 100, score=0.5),   # filtered
    ]
    result = engine.detect(MagicMock())

    assert len(result) == 1
    assert result[0].confidence == 0.9


def test_detect_filters_small_faces(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("face.detection_confidence", "0.0", "float")
    settings_cache.cache.set("face.min_face_size", "50", "int")

    engine._app.get.return_value = [
        _fake_face(0, 0, 100, 100, score=0.9),  # 100x100 — passes
        _fake_face(0, 0, 30, 30, score=0.9),    # 30x30 — filtered
    ]
    result = engine.detect(MagicMock())

    assert len(result) == 1
    assert result[0].bbox[2] == 100  # w


def test_detect_empty_image(tmp_path):
    engine = _make_engine(tmp_path)
    engine._app.get.return_value = []
    assert engine.detect(MagicMock()) == []


def test_model_name_property(tmp_path):
    engine = _make_engine(tmp_path)
    assert engine.model_name == "buffalo_l"
