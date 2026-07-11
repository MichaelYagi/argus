"""Ultralytics YOLO wrapper for object detection."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import settings_cache
from app.inference.device import torch_device

logger = logging.getLogger(__name__)


@dataclass
class ObjectDetection:
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    class_name: str
    class_id: int


class ObjectEngine:
    def __init__(self, model_name: str, model_path: Path) -> None:
        self._model_name = model_name
        self._is_world   = "world" in model_name.lower()
        self._device     = torch_device(mps=False)

        kind = "YOLOWorld" if self._is_world else ("RTDETR" if "rtdetr" in model_name.lower() else "YOLO")
        logger.debug("loading object engine: model=%s kind=%s device=%s", model_name, kind, self._device)
        t0 = time.monotonic()
        if self._is_world:
            from ultralytics import YOLOWorld
            self._model = YOLOWorld(str(model_path))
            self._apply_world_classes()
        elif "rtdetr" in model_name.lower():
            # RT-DETR is a transformer detector; it needs Ultralytics' RTDETR loader,
            # but its results expose the same .boxes interface, so detect() is unchanged.
            from ultralytics import RTDETR
            self._model = RTDETR(str(model_path))
        else:
            from ultralytics import YOLO
            self._model = YOLO(str(model_path))
        logger.debug("object engine ready: model=%s in %.1fs", model_name, time.monotonic() - t0)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_world(self) -> bool:
        return self._is_world

    def _apply_world_classes(self) -> None:
        raw = settings_cache.cache.get_or("object.world_classes", "")
        classes = [c.strip() for c in raw.split(",") if c.strip()]
        if classes:
            self._model.set_classes(classes)
            self._world_classes_raw = raw

    def detect(self, image: Any) -> list[ObjectDetection]:
        min_conf = settings_cache.cache.get_or("object.detection_confidence", 0.5)
        iou      = settings_cache.cache.get_or("object.iou_threshold", 0.45)
        t0 = time.monotonic()

        if self._is_world:
            # Only re-encode vocabulary if it changed since last call
            current_raw = settings_cache.cache.get_or("object.world_classes", "")
            if current_raw != getattr(self, "_world_classes_raw", None):
                self._apply_world_classes()
            results = self._model.predict(
                image, conf=min_conf, iou=iou, device=self._device, verbose=False
            )
        else:
            results = self._model(
                image, conf=min_conf, iou=iou, device=self._device, verbose=False
            )

        classes_enabled = settings_cache.cache.get_or("object.classes_enabled", "*")

        detections: list[ObjectDetection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                cls_id   = int(box.cls)
                cls_name = (names[cls_id] if isinstance(names, list)
                            else names.get(cls_id, str(cls_id)))
                if not self._is_world and classes_enabled != "*":
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
        logger.debug(
            "object detect: %d detections (min_conf=%.2f iou=%.2f) in %.0fms",
            len(detections), min_conf, iou, (time.monotonic() - t0) * 1000,
        )
        return detections
