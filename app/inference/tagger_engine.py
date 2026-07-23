"""RAM++ + Grounding DINO compound tagger engine.

RAM++ (Recognize Anything Plus Model, ram_plus in the package) scans the whole image
and generates a list of descriptive keyword tags. Grounding DINO then localizes each
tag with a bounding box.

The two outputs are surfaced separately:
  - image-level tags  → source_images.scene_tags (JSON array)
  - per-instance dets → detections rows, one per localized tag instance

This engine satisfies the standard object engine interface (detect()) so it can slot
into the existing pipeline unchanged, and additionally exposes detect_with_tags() so
the detect routes can persist and return the image-level tags.

Compatibility shim: transformers 5.x removed apply_chunking_to_forward,
find_pruneable_heads_and_indices, and prune_linear_layer from modeling_utils; the ram
package still imports them from there. The shim is applied lazily inside __init__ and
_run_ram, before any ram import, and is idempotent.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

from app.inference.device import torch_device
from app.inference.object_engine import ObjectDetection

DIR_NAME = "ram-plus-plus-grounding-dino"
RAM_REPO_ID = "xinyu1205/recognize-anything-plus-model"
RAM_FILENAME = "ram_plus_swin_large_14m.pth"
DINO_REPO_ID = "IDEA-Research/grounding-dino-base"
DINO_DIR_NAME = "grounding-dino-base"
RAM_IMAGE_SIZE = 384
BOX_THRESHOLD = 0.35
TEXT_THRESHOLD = 0.25


def _patch_ram_source() -> None:
    """Idempotent: rewrite the one line in ram/models/utils.py that uses
    additional_special_tokens_ids, removed in transformers 5.x.
    Uses sys.path search to locate the file without triggering any ram imports.
    Must be called before any 'import ram' statement.
    """
    import sys
    old = "tokenizer.enc_token_id = tokenizer.additional_special_tokens_ids[0]"
    new = "tokenizer.enc_token_id = tokenizer.convert_tokens_to_ids(tokenizer.additional_special_tokens)[0]"
    for entry in sys.path:
        if not isinstance(entry, str):
            continue
        candidate = Path(entry) / "ram" / "models" / "utils.py"
        if candidate.is_file():
            try:
                text = candidate.read_text()
                if old in text:
                    candidate.write_text(text.replace(old, new))
            except OSError:
                pass
            return


def _patch_transformers_modeling_utils() -> None:
    """Idempotent shim: restore symbols removed from transformers.modeling_utils in 5.x.

    The ram package's bert.py imports these from transformers.modeling_utils. In 5.x
    the first two were moved to pytorch_utils; find_pruneable_heads_and_indices was
    removed entirely. This must be called before any 'import ram' statement.
    """
    import torch
    import transformers.modeling_utils as _tmu
    import transformers.pytorch_utils as _tpu

    for _name in ("apply_chunking_to_forward", "prune_linear_layer"):
        if not hasattr(_tmu, _name):
            setattr(_tmu, _name, getattr(_tpu, _name))

    if not hasattr(_tmu, "find_pruneable_heads_and_indices"):
        def _find_pruneable(
            heads: list, n_heads: int, head_size: int, already_pruned_heads: set
        ):
            mask = torch.ones(n_heads, head_size)
            heads = set(heads) - already_pruned_heads
            for head in heads:
                head = head - sum(1 if h < head else 0 for h in already_pruned_heads)
                mask[head] = 0
            mask = mask.view(-1).contiguous().eq(1)
            index = torch.arange(len(mask))[mask].long()
            return heads, index

        _tmu.find_pruneable_heads_and_indices = _find_pruneable


class TaggerEngine:
    """Compound engine: RAM++ generates keyword tags, Grounding DINO localizes them."""

    has_scene_tags: bool = True

    def __init__(
        self,
        models_dir: Path,
        *,
        ram_model: Any = None,
        dino_model: Any = None,
        dino_processor: Any = None,
        device: str | None = None,
    ) -> None:
        self._device = device or torch_device()

        # Test-seam: accept pre-loaded models to avoid disk I/O in tests.
        if ram_model is not None and dino_model is not None and dino_processor is not None:
            self._ram = ram_model
            self._dino = dino_model
            self._dino_proc = dino_processor
            return

        base_dir = Path(models_dir) / DIR_NAME
        base_dir.mkdir(parents=True, exist_ok=True)

        # --- RAM++ ---------------------------------------------------------
        _patch_ram_source()           # edits utils.py on disk before any ram import
        _patch_transformers_modeling_utils()  # patches transformers symbols in memory

        try:
            from ram.models import ram_plus
        except ImportError:
            raise ImportError(
                "The 'ram' package is required for the RAM++ + Grounding DINO engine. "
                "Install it with: "
                "pip install ram @ git+https://github.com/xinyu1205/recognize-anything.git"
            ) from None

        try:
            import transformers as _tf
            _tf.logging.set_verbosity_error()
        except Exception:
            pass

        from huggingface_hub import hf_hub_download
        ram_ckpt = hf_hub_download(
            repo_id=RAM_REPO_ID,
            filename=RAM_FILENAME,
            local_dir=str(base_dir),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self._ram = ram_plus(pretrained=ram_ckpt, image_size=RAM_IMAGE_SIZE, vit="swin_l")
        self._ram.eval()
        self._ram = self._ram.to(self._device)

        # --- Grounding DINO ------------------------------------------------
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        dino_dir = base_dir / DINO_DIR_NAME
        snapshot_download(DINO_REPO_ID, local_dir=str(dino_dir))

        self._dino_proc = AutoProcessor.from_pretrained(str(dino_dir))
        self._dino = (
            AutoModelForZeroShotObjectDetection
            .from_pretrained(str(dino_dir))
            .to(self._device)
            .eval()
        )

    # ------------------------------------------------------------------
    # Standard object engine interface
    # ------------------------------------------------------------------

    def detect(self, image: Any) -> list[ObjectDetection]:
        """Run the full pipeline; image-level tags are discarded."""
        _, dets = self.detect_with_tags(image)
        return dets

    # ------------------------------------------------------------------
    # Extended interface
    # ------------------------------------------------------------------

    def detect_with_tags(
        self, image: Any
    ) -> tuple[list[str], list[ObjectDetection]]:
        """Return (scene_tags, per-instance detections)."""
        tags = self._run_ram(image)
        dets = self._run_dino(image, tags) if tags else []
        return tags, dets

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_ram(self, image: Any) -> list[str]:
        _patch_transformers_modeling_utils()
        import torch
        from PIL import Image
        from ram.inference import inference_ram
        from ram.transform import get_transform

        pil = Image.fromarray(image)
        transform = get_transform(image_size=RAM_IMAGE_SIZE)
        img_t = transform(pil).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            english_tags, _ = inference_ram(img_t, self._ram)
        return [t.strip() for t in english_tags.split(" | ") if t.strip()]

    def _run_dino(self, image: Any, tags: list[str]) -> list[ObjectDetection]:
        import torch
        from PIL import Image

        pil = Image.fromarray(image)
        width, height = pil.size

        # Grounding DINO expects lowercase dot-separated text
        text_prompt = ". ".join(t.lower() for t in tags) + "."

        inputs = self._dino_proc(
            images=pil, text=text_prompt, return_tensors="pt"
        ).to(self._device)
        with torch.inference_mode():
            outputs = self._dino(**inputs)

        results = self._dino_proc.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
            target_sizes=[(height, width)],
        )
        # Build a lookup so compound labels like "person woman" can be resolved back
        # to a single canonical tag. Grounding DINO sometimes spans multiple prompt
        # tokens for one box when the model scores adjacent tags similarly.
        tag_lower = [t.lower() for t in tags]
        tag_set = set(tag_lower)

        def _canonical(raw: str) -> str:
            raw = raw.strip().lower()
            if raw in tag_set:
                return raw
            # compound: pick the word that appears latest in the tag list
            # (later words tend to be more specific in RAM++ output order)
            words = raw.split()
            for word in reversed(words):
                if word in tag_set:
                    return word
            return words[-1] if words else raw

        r = results[0]
        dets: list[ObjectDetection] = []
        for score, label, box in zip(r["scores"], r["labels"], r["boxes"]):
            x1, y1, x2, y2 = [int(v) for v in box.tolist()]
            dets.append(ObjectDetection(
                bbox=(x1, y1, x2 - x1, y2 - y1),
                confidence=round(float(score), 4),
                class_name=_canonical(label),
                class_id=0,
            ))
        return dets
