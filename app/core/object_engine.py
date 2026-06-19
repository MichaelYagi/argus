"""Ultralytics YOLO wrapper for object detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import settings_cache


@dataclass
class ObjectDetection:
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    class_name: str
    class_id: int


def _object_device() -> str:
    """Return torch device string: 'cuda:0' or 'cpu'."""
    if not settings_cache.cache.get_or("system.use_gpu", True):
        return "cpu"
    try:
        import torch
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class ObjectEngine:
    def __init__(self, model_name: str, model_path: Path) -> None:
        from ultralytics import YOLO

        self._model_name = model_name
        self._model = YOLO(str(model_path))
        self._device = _object_device()

    @property
    def model_name(self) -> str:
        return self._model_name

    def detect(self, image: Any) -> list[ObjectDetection]:
        min_conf = settings_cache.cache.get_or("object.detection_confidence", 0.5)
        iou = settings_cache.cache.get_or("object.iou_threshold", 0.45)
        classes_enabled = settings_cache.cache.get_or("object.classes_enabled", "*")

        results = self._model(image, conf=min_conf, iou=iou, device=self._device, verbose=False)

        detections: list[ObjectDetection] = []
        for result in results:
            names: dict[int, str] = result.names
            for box in result.boxes:
                cls_id = int(box.cls)
                cls_name = names.get(cls_id, str(cls_id))
                if classes_enabled != "*":
                    enabled = {c.strip() for c in classes_enabled.split(",")}
                    if cls_name not in enabled:
                        continue
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
                detections.append(ObjectDetection(
                    bbox=(x1, y1, x2 - x1, y2 - y1),
                    confidence=float(box.conf),
                    class_name=cls_name,
                    class_id=cls_id,
                ))
        return detections
