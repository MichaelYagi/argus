"""Unit tests for FlorenceEngine — model/processor are injected so the test
runs without transformers/huggingface_hub installed."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from app.core import settings_cache
from app.core.florence_engine import TASK, FlorenceEngine, _florence_device
from app.core.object_engine import ObjectDetection


def _make_engine(parsed: dict) -> FlorenceEngine:
    """Build an engine whose processor returns a fixed <OD> parse.

    numpy/PIL/torch are MagicMock-stubbed in the minimal dev install (see
    tests/conftest.py), so configure the PIL stub to yield a real (w, h) size.
    """
    sys.modules["PIL"].Image.fromarray.return_value.size = (100, 100)
    processor = MagicMock()
    # processor(text=..., images=...).to(device) -> a mapping spread into generate(**inputs)
    processor.return_value.to.return_value = {"input_ids": MagicMock(), "pixel_values": MagicMock()}
    processor.batch_decode.return_value = ["<OD>ignored-raw-text"]
    processor.post_process_generation.return_value = parsed
    model = MagicMock()
    return FlorenceEngine(MagicMock(), model=model, processor=processor, device="cpu")


def _img() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------

def test_device_cpu_when_use_gpu_false():
    settings_cache.cache.set("system.use_gpu", "false", "bool")
    assert _florence_device() == "cpu"


def test_device_cuda_when_available():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("torch.cuda.is_available", return_value=True):
        assert _florence_device() == "cuda:0"


def test_device_mps_when_cuda_absent_mps_present():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("torch.cuda.is_available", return_value=False), \
         patch("torch.backends.mps.is_available", return_value=True):
        assert _florence_device() == "mps"


def test_device_cpu_when_no_accelerator():
    settings_cache.cache.set("system.use_gpu", "true", "bool")
    with patch("torch.cuda.is_available", return_value=False), \
         patch("torch.backends.mps.is_available", return_value=False):
        assert _florence_device() == "cpu"


# ---------------------------------------------------------------------------
# Detection / OD parsing
# ---------------------------------------------------------------------------

def test_detect_maps_od_to_object_detections():
    settings_cache.cache.set("object.classes_enabled", "*", "string")
    parsed = {TASK: {"bboxes": [[10, 20, 110, 120]], "labels": ["dog"]}}
    engine = _make_engine(parsed)

    result = engine.detect(_img())

    assert len(result) == 1
    d = result[0]
    assert isinstance(d, ObjectDetection)
    assert d.bbox == (10, 20, 100, 100)  # xyxy -> xywh
    assert d.confidence == 1.0           # OD has no per-box score
    assert d.class_name == "dog"
    assert d.class_id == 0


def test_detect_empty():
    settings_cache.cache.set("object.classes_enabled", "*", "string")
    engine = _make_engine({TASK: {"bboxes": [], "labels": []}})
    assert engine.detect(_img()) == []


def test_detect_respects_classes_enabled_allowlist():
    settings_cache.cache.set("object.classes_enabled", "dog,cat", "string")
    parsed = {TASK: {
        "bboxes": [[0, 0, 10, 10], [0, 0, 10, 10]],
        "labels": ["dog", "car"],
    }}
    engine = _make_engine(parsed)

    result = engine.detect(_img())
    assert len(result) == 1
    assert result[0].class_name == "dog"


def test_model_name_property():
    engine = _make_engine({TASK: {"bboxes": [], "labels": []}})
    assert engine.model_name == "florence-2-base"
