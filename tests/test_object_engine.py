from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core import settings_cache
from app.inference.device import torch_device
from app.inference.object_engine import ObjectDetection, ObjectEngine


def _fake_box(x1: float, y1: float, x2: float, y2: float, conf: float, cls_id: int) -> MagicMock:
    box = MagicMock()
    box.xyxy = [[x1, y1, x2, y2]]
    box.conf = conf
    box.cls = cls_id
    return box


def _make_engine(tmp_path: Path) -> ObjectEngine:
    with patch("ultralytics.YOLO") as mock_cls:
        mock_cls.return_value = MagicMock()
        engine = ObjectEngine("yolov8n", tmp_path / "yolov8n.pt")
    return engine


# ---------------------------------------------------------------------------
# GPU selection
# ---------------------------------------------------------------------------

def test_device_cpu_when_use_gpu_false():
    settings_cache.cache.set("system.use_gpu", "false", "bool")
    assert torch_device(mps=False) == "cpu"


def test_device_cuda_when_available():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("torch.cuda.is_available", return_value=True):
        assert torch_device(mps=False) == "cuda:0"


def test_device_cpu_when_cuda_unavailable():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("torch.cuda.is_available", return_value=False):
        assert torch_device(mps=False) == "cpu"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _result_with_boxes(boxes: list, names: dict) -> MagicMock:
    result = MagicMock()
    result.names = names
    result.boxes = boxes
    return result


def test_detect_returns_object_detections(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("object.detection_confidence", "0.5", "float")
    settings_cache.cache.set("object.iou_threshold", "0.45", "float")
    settings_cache.cache.set("object.classes_enabled", "*", "string")

    box = _fake_box(10, 20, 110, 120, conf=0.9, cls_id=0)
    engine._model.return_value = [_result_with_boxes([box], {0: "dog"})]

    result = engine.detect(MagicMock())

    assert len(result) == 1
    d = result[0]
    assert isinstance(d, ObjectDetection)
    assert d.bbox == (10, 20, 100, 100)
    assert d.confidence == 0.9
    assert d.class_name == "dog"
    assert d.class_id == 0


def test_detect_bbox_conversion(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("object.detection_confidence", "0.0", "float")
    settings_cache.cache.set("object.iou_threshold", "0.45", "float")
    settings_cache.cache.set("object.classes_enabled", "*", "string")

    box = _fake_box(5, 15, 55, 115, conf=0.8, cls_id=1)
    engine._model.return_value = [_result_with_boxes([box], {1: "cat"})]

    result = engine.detect(MagicMock())
    assert result[0].bbox == (5, 15, 50, 100)


def test_detect_filters_by_class_name(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("object.detection_confidence", "0.0", "float")
    settings_cache.cache.set("object.iou_threshold", "0.45", "float")
    settings_cache.cache.set("object.classes_enabled", "dog,cat", "string")

    boxes = [
        _fake_box(0, 0, 50, 50, conf=0.9, cls_id=0),  # dog — included
        _fake_box(0, 0, 50, 50, conf=0.9, cls_id=2),  # car — filtered
    ]
    names = {0: "dog", 2: "car"}
    engine._model.return_value = [_result_with_boxes(boxes, names)]

    result = engine.detect(MagicMock())
    assert len(result) == 1
    assert result[0].class_name == "dog"


def test_detect_all_classes_when_star(tmp_path):
    engine = _make_engine(tmp_path)
    settings_cache.cache.set("object.classes_enabled", "*", "string")
    settings_cache.cache.set("object.detection_confidence", "0.0", "float")
    settings_cache.cache.set("object.iou_threshold", "0.45", "float")

    boxes = [
        _fake_box(0, 0, 50, 50, conf=0.9, cls_id=0),
        _fake_box(0, 0, 50, 50, conf=0.9, cls_id=5),
    ]
    engine._model.return_value = [_result_with_boxes(boxes, {0: "dog", 5: "bus"})]

    result = engine.detect(MagicMock())
    assert len(result) == 2


def test_detect_empty_image(tmp_path):
    engine = _make_engine(tmp_path)
    engine._model.return_value = [_result_with_boxes([], {})]
    assert engine.detect(MagicMock()) == []


def test_model_name_property(tmp_path):
    engine = _make_engine(tmp_path)
    assert engine.model_name == "yolov8n"
