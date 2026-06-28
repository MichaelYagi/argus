"""Florence-2 wrapper exposing grounded open-vocabulary object detection.

Florence-2 is registered as an ``object`` model: its ``<OD>`` task returns
labelled bounding boxes, so it produces the same ``ObjectDetection`` records as
the YOLO engines and flows through the existing detection/crop/gallery pipeline
unchanged.

Why PyTorch and not onnxruntime: Florence-2's ONNX export splits into four
graphs (embed_tokens, vision_encoder, encoder_model, decoder_model_merged) and
driving the decoder's cache/branch wiring by hand from raw onnxruntime is not
practical (and optimum has no Florence-2 class). The transformers PyTorch path
implements that generation loop internally, so that is what we use. torch is
already in the stack via ultralytics.

We use the transformers-native Florence-2 (``Florence2ForConditionalGeneration``,
checkpoint ``florence-community/Florence-2-base``) with NO trust_remote_code.
The original ``microsoft/*`` checkpoints ship modeling code on the Hub that
references ``forced_bos_token_id`` (removed in transformers v5), so they crash on
current transformers; the native port is maintained against the library instead.
Requires transformers >= 4.57 (when native Florence-2 landed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core import settings_cache
from app.core.object_engine import ObjectDetection

# Hub repo and the on-disk directory name under models_dir().
REPO_ID = "florence-community/Florence-2-base"
DIR_NAME = "florence-2-base"

# Generation knobs. num_beams=3 matches the canonical OD example and gives the
# best label quality; drop to 1 (greedy) to roughly halve decoder memory on the
# 8GB M1 target. max_new_tokens must be large enough to emit every box in a
# crowded scene.
TASK = "<OD>"
NUM_BEAMS = 3
MAX_NEW_TOKENS = 1024


def _florence_device() -> str:
    """Return a torch device string honoring system.use_gpu: cuda, mps, or cpu."""
    if not settings_cache.cache.get_or("system.use_gpu", True):
        return "cpu"
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda:0"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class FlorenceEngine:
    def __init__(
        self,
        models_dir: Path,
        *,
        model: Any = None,
        processor: Any = None,
        device: str | None = None,
    ) -> None:
        # model/processor injection is a test seam and lets a preloaded instance
        # be reused; normal callers pass only models_dir.
        self._device = device or _florence_device()
        if model is not None and processor is not None:
            self._model = model
            self._processor = processor
            return

        local_dir = Path(models_dir) / DIR_NAME
        from huggingface_hub import snapshot_download

        snapshot_download(REPO_ID, local_dir=str(local_dir))

        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        self._model = (
            Florence2ForConditionalGeneration.from_pretrained(str(local_dir))
            .to(self._device)
            .eval()
        )
        self._processor = AutoProcessor.from_pretrained(str(local_dir))

    @property
    def model_name(self) -> str:
        return DIR_NAME

    def detect(self, image: Any) -> list[ObjectDetection]:
        """Run <OD> on an RGB numpy array and return labelled boxes."""
        import torch
        from PIL import Image

        pil = Image.fromarray(image)
        width, height = pil.size

        inputs = self._processor(text=TASK, images=pil, return_tensors="pt").to(self._device)

        with torch.inference_mode():
            generated_ids = self._model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                num_beams=NUM_BEAMS,
            )

        text = self._processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = self._processor.post_process_generation(
            text, task=TASK, image_size=(width, height)
        )
        od = parsed.get(TASK, {})
        bboxes = od.get("bboxes", [])
        labels = od.get("labels", [])

        # Florence OD is open-vocabulary and not promptable, but honor a configured
        # object.classes_enabled allow-list if one is set (post-filter on label).
        classes_enabled = settings_cache.cache.get_or("object.classes_enabled", "*")
        enabled = (
            None
            if classes_enabled == "*"
            else {c.strip().lower() for c in classes_enabled.split(",") if c.strip()}
        )

        detections: list[ObjectDetection] = []
        for (x1, y1, x2, y2), label in zip(bboxes, labels):
            if enabled is not None and label.lower() not in enabled:
                continue
            x1i, y1i, x2i, y2i = int(x1), int(y1), int(x2), int(y2)
            detections.append(
                ObjectDetection(
                    bbox=(x1i, y1i, x2i - x1i, y2i - y1i),
                    confidence=1.0,  # Florence OD emits no per-box score
                    class_name=label,
                    class_id=0,
                )
            )
        return detections
