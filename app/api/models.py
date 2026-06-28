"""Model management — list, download, activate, delete."""

from __future__ import annotations

import shutil
import threading
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.auth import require_admin
from app.core.engine_registry import registry
from app.core.paths import models_dir
from app.db import store

router = APIRouter()

# Engines loaded during download but not yet activated. Cleared on activation
# or if the model is deleted. In-process only — cleared on restart.
_loaded: dict[int, Any] = {}
_loaded_lock = threading.Lock()

# Download progress per model_id
_progress: dict[int, dict] = {}


def downloading_ids() -> set[int]:
    """Model IDs with an in-flight download. In-process state, reset on restart —
    used so a page reload can re-attach the progress spinner/polling."""
    return {mid for mid, p in _progress.items() if p.get("status") == "downloading"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/models")
async def list_models(
    type: Optional[str] = Query(None),
    user_id: int = Depends(require_admin),
):
    if type and type not in ("face", "object", "clip"):
        raise HTTPException(400, "type must be 'face', 'object', or 'clip'")
    return [_fmt(r) for r in store.list_models(model_type=type)]


@router.get("/api/models/{model_id}")
async def get_model(model_id: int, user_id: int = Depends(require_admin)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    return _fmt(row)


@router.post("/api/models/{model_id}/download", status_code=202)
async def download_model(
    model_id: int,
    background: BackgroundTasks,
    user_id: int = Depends(require_admin),
):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if row["is_downloaded"]:
        return {"model_id": model_id, "status": "already_downloaded"}
    if _progress.get(model_id, {}).get("status") == "downloading":
        return {"model_id": model_id, "status": "downloading"}

    _progress[model_id] = {"status": "downloading", "error": None}
    background.add_task(_run_download, model_id, row["type"], row["name"])
    return {"model_id": model_id, "status": "downloading"}


@router.get("/api/models/{model_id}/download/status")
async def download_status(model_id: int, user_id: int = Depends(require_admin)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if row["is_downloaded"]:
        return {"model_id": model_id, "status": "complete"}
    prog = _progress.get(model_id, {"status": "idle", "error": None})
    return {"model_id": model_id, **prog}


@router.put("/api/models/{model_id}/activate")
async def activate_model(model_id: int, user_id: int = Depends(require_admin)):
    """Hot-swap the active engine. Synchronous — loads from disk if not cached."""
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if not row["is_downloaded"]:
        raise HTTPException(409, "Model not downloaded. Trigger a download first.")

    with _loaded_lock:
        engine = _loaded.pop(model_id, None)

    if engine is None:
        # Not cached — load from disk (e.g. after a restart)
        engine = _load_engine(row["type"], row["name"])

    if row["type"] == "face":
        registry.swap_face_engine(engine)
        from app.core import face_index as _fi
        _fi.build_all(model_id)
    elif row["type"] == "clip":
        registry.swap_tagging_engine(engine)
        from app.core import keyword_jobs
        # Building the ~21k-word text matrix can take many minutes (especially
        # ViT-L-14 on CPU), so it runs inside the background job rather than blocking
        # this request. start_backfill builds the matrix first, then encodes images.
        # Until the matrix is ready, keyword scoring simply returns empty.
        keyword_jobs.start_backfill(model_id)
    else:
        registry.swap_object_engine(engine)

    store.set_model_active(model_id, row["type"])
    return _fmt(store.get_model(model_id))


@router.delete("/api/models/{model_id}", status_code=204)
async def delete_model(model_id: int, user_id: int = Depends(require_admin)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if not row["is_downloaded"]:
        raise HTTPException(409, "Model is not downloaded")

    if row["is_active"]:
        if row["type"] == "face":
            registry.swap_face_engine(None)
        elif row["type"] == "clip":
            registry.swap_tagging_engine(None)
            from app.core import keyword_index
            keyword_index.reset()
        else:
            registry.swap_object_engine(None)

    _delete_files(row["type"], row["name"])

    with _loaded_lock:
        _loaded.pop(model_id, None)

    store.set_model_downloaded(model_id, False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_download(model_id: int, model_type: str, model_name: str) -> None:
    """Background task: download weights and cache the loaded engine."""
    try:
        engine = _load_engine(model_type, model_name)
        with _loaded_lock:
            _loaded[model_id] = engine
        store.set_model_downloaded(model_id, True)
        _progress[model_id] = {"status": "complete", "error": None}
    except Exception as exc:
        _progress[model_id] = {"status": "failed", "error": str(exc)}


def _load_engine(model_type: str, model_name: str) -> Any:
    """Load (and if necessary download) an engine. Slow for large models."""
    if model_type == "face":
        from app.core.face_engine import FaceEngine
        return FaceEngine(model_name, models_dir())
    if model_type == "clip":
        from app.core.tagging_engine import TaggingEngine
        clip_dir = models_dir() / "clip" / model_name
        _ensure_clip_assets(model_name, clip_dir)
        return TaggingEngine(model_name, clip_dir)
    from app.core.object_engine import ObjectEngine
    return ObjectEngine(model_name, models_dir() / f"{model_name}.pt")


# CLIP assets are the ONNX image/text encoders + shared BPE vocab. They are produced
# by exporting an OpenCLIP checkpoint to ONNX. When a CLIP model is downloaded and its
# assets are missing, Argus exports them locally via torch/open_clip (download-time
# only; inference always runs on onnxruntime). open_clip pulls the checkpoint from its
# own public host, so the Download button is self-service like YOLO/buffalo weights.
# ARGUS_CLIP_ASSET_BASE is an optional fast path: if set, the pre-exported ONNX files
# are fetched over HTTP instead, skipping the torch export (useful for air-gapped or
# torch-free deployments that pre-host the assets).
_CLIP_ASSET_FILES = ("image_encoder.onnx", "text_encoder.onnx", "bpe_simple_vocab_16e6.txt.gz")


def _ensure_clip_assets(model_name: str, clip_dir) -> None:
    import os

    missing = [f for f in _CLIP_ASSET_FILES if not (clip_dir / f).exists()]
    if not missing:
        return
    base = os.environ.get("ARGUS_CLIP_ASSET_BASE", "").rstrip("/")
    if base:
        _download_clip_assets(model_name, clip_dir, base, missing)
        return
    # No pre-hosted assets: export from the OpenCLIP checkpoint. Slow on first run
    # (downloads the checkpoint, then converts), cached on disk afterward.
    from app.core.clip_export import export_model
    export_model(model_name, clip_dir)


def _download_clip_assets(model_name: str, clip_dir, base: str, missing) -> None:
    import httpx
    clip_dir.mkdir(parents=True, exist_ok=True)
    for fname in missing:
        url = f"{base}/{model_name}/{fname}"
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
            r.raise_for_status()
            with open(clip_dir / fname, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)


def _delete_files(model_type: str, model_name: str) -> None:
    if model_type == "face":
        path = models_dir() / "models" / model_name
        if path.exists():
            shutil.rmtree(path)
    elif model_type == "clip":
        path = models_dir() / "clip" / model_name
        if path.exists():
            shutil.rmtree(path)
    else:
        path = models_dir() / f"{model_name}.pt"
        if path.exists():
            path.unlink()


def _fmt(row) -> dict:
    try:
        description = row["description"]
    except (IndexError, KeyError):
        description = None
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "embedding_dim": row["embedding_dim"],
        "description": description,
        "is_downloaded": bool(row["is_downloaded"]),
        "is_active": bool(row["is_active"]),
    }
