"""Keyword tagging compute helpers and background jobs.

Two shared entry points used by the detect pipeline:
  - tag_image(...)    : embed a freshly-scanned stored image, persist its vector and
                        keywords, and return them (the sync/async detect path).

Two background jobs, single-flight and latest-vocab-wins:
  - start_backfill(model_id) : re-encode every stored image lacking a vector for the
                               active CLIP model (slow; on model activate/swap).
  - start_retag(model_id)    : re-score persisted vectors against the rebuilt matrix
                               (cheap; on vocabulary or prompt-template change).

A re-tag waits for any in-progress backfill, since it needs image vectors to exist.
Execution is plain background threads (no Celery/Redis), consistent with the project.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.core import keyword_index
from app.core.engine_registry import registry
from app.core.paths import sources_dir
from app.db import store

log = logging.getLogger("app.keyword_jobs")

_job_lock = threading.Lock()
_state: dict = {"running": None, "progress": 0, "total": 0, "version": None}


# ---------------------------------------------------------------------------
# Per-image tagging (shared with the detect pipeline)
# ---------------------------------------------------------------------------

def active_model() -> Any:
    return store.get_active_model("clip")


def tag_image(source_image_id: int, user_id: int, environment_id: int, img: Any,
              boxes: list | None = None) -> list[dict]:
    """Embed a stored image, persist its vector + keywords, and return the keywords.
    When boxes (detected face/object regions) are given, keywords are computed with
    per-region pooling. No-op (returns []) when no CLIP model is active."""
    model = active_model()
    engine = registry.get_tagging_engine()
    if model is None or engine is None:
        return []
    model_id = model["id"]
    keyword_index.build(model_id)
    import numpy as np

    vec = engine.embed_image(img)
    store.upsert_image_embedding(
        source_image_id, user_id, model_id,
        np.asarray(vec, dtype=np.float32).tobytes(), int(vec.shape[0]), environment_id,
    )
    version = store.get_vocab_version()
    if boxes:
        pairs = keyword_index.score_pooled(region_vectors(engine, img, boxes))
    else:
        pairs = keyword_index.score(vec)
    store.set_image_keywords(source_image_id, user_id, model_id, version, pairs, environment_id)
    return [{"keyword": kw, "score": round(float(sc), 4)} for kw, sc in pairs]


def region_vectors(engine, img, boxes, max_regions: int = 10) -> list:
    """Embed the whole image plus each detected region crop, for per-region tagging.
    boxes are (x, y, w, h). Returns a list of image vectors (whole image first); pass to
    keyword_index.score_pooled so a prominent-in-its-crop object can still earn a tag."""
    vecs = [engine.embed_image(img)]
    try:
        W, H = img.size
    except Exception:
        return vecs
    for (x, y, w, h) in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)[:max_regions]:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(W, int(x + w)), min(H, int(y + h))
        if x1 - x0 < 16 or y1 - y0 < 16:  # skip tiny crops
            continue
        try:
            vecs.append(engine.embed_image(img.crop((x0, y0, x1, y1))))
        except Exception as exc:
            log.debug("region crop embed failed: %s", exc)
    return vecs


def _retag_one(row, model_id: int, version: int) -> None:
    import numpy as np

    vec = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
    pairs = keyword_index.score(vec)
    store.set_image_keywords(
        row["source_image_id"], row["user_id"], model_id, version, pairs, row["environment_id"],
    )


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------

def status() -> dict:
    with _job_lock:
        return dict(_state)


def _set(**kw) -> None:
    with _job_lock:
        _state.update(kw)


def start_backfill(model_id: int) -> None:
    """Re-encode all stored images lacking a vector for this model."""
    threading.Thread(target=_run_backfill, args=(model_id,), daemon=True).start()


def _run_backfill(model_id: int) -> None:
    try:
        # The matrix build is the long pole on first activation (encoding the whole
        # vocabulary through the text encoder). Surface it as its own phase so the UI
        # poller shows activity instead of a silent multi-minute gap.
        _set(running="index", progress=0, total=0, version=store.get_vocab_version())
        keyword_index.build(model_id)
        engine = registry.get_tagging_engine()
        if engine is None:
            return
        rows = store.source_images_missing_embedding(model_id)
        _set(running="backfill", progress=0, total=len(rows), version=store.get_vocab_version())
        from PIL import Image
        for i, r in enumerate(rows):
            try:
                with Image.open(sources_dir() / r["file_path"]) as im:
                    im.load()
                    tag_image(r["id"], r["user_id"], r["environment_id"], im)
            except Exception as exc:  # skip unreadable images, keep going
                log.warning("backfill skipped image %s: %s", r["id"], exc)
            _set(progress=i + 1)
        log.info("keyword backfill complete: model=%s images=%d", model_id, len(rows))
    finally:
        # Once vectors exist, run a re-tag to make keywords consistent with the latest vocab.
        _set(running=None)
        start_retag(model_id)


def start_retag(model_id: int) -> None:
    """Re-score persisted vectors against the current matrix (cheap)."""
    threading.Thread(target=_run_retag, args=(model_id,), daemon=True).start()


def _run_retag(model_id: int) -> None:
    # Wait for any in-progress backfill — re-tag needs vectors to exist.
    while status().get("running") == "backfill":
        threading.Event().wait(0.5)
    try:
        keyword_index.build(model_id)
        version = store.get_vocab_version()
        rows = store.all_image_embeddings(model_id)
        _set(running="retag", progress=0, total=len(rows), version=version)
        for i, r in enumerate(rows):
            # Latest-wins: bail if a newer vocab version has landed.
            if store.get_vocab_version() != version:
                log.info("retag superseded by newer vocab version; stopping")
                break
            _retag_one(r, model_id, version)
            _set(progress=i + 1)
        log.info("keyword retag complete: model=%s images=%d version=%s", model_id, len(rows), version)
    finally:
        _set(running=None)


def trigger_vocab_change() -> None:
    """Called after a vocabulary or prompt-template edit: rebuild the matrix and
    re-tag the library against the new version, if a CLIP model is active."""
    model = active_model()
    if model is None:
        return
    keyword_index.build(model["id"])
    start_retag(model["id"])
