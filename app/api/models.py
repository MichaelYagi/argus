"""Model management — list, download, activate, delete."""

from __future__ import annotations

import shutil
import threading
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.auth import require_auth
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/models")
async def list_models(
    type: Optional[str] = Query(None),
    user_id: int = Depends(require_auth),
):
    if type and type not in ("face", "object"):
        raise HTTPException(400, "type must be 'face' or 'object'")
    return [_fmt(r) for r in store.list_models(model_type=type)]


@router.get("/api/models/{model_id}")
async def get_model(model_id: int, user_id: int = Depends(require_auth)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    return _fmt(row)


@router.post("/api/models/{model_id}/download", status_code=202)
async def download_model(
    model_id: int,
    background: BackgroundTasks,
    user_id: int = Depends(require_auth),
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
async def download_status(model_id: int, user_id: int = Depends(require_auth)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if row["is_downloaded"]:
        return {"model_id": model_id, "status": "complete"}
    prog = _progress.get(model_id, {"status": "idle", "error": None})
    return {"model_id": model_id, **prog}


@router.put("/api/models/{model_id}/activate")
async def activate_model(model_id: int, user_id: int = Depends(require_auth)):
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
    else:
        registry.swap_object_engine(engine)

    store.set_model_active(model_id, row["type"])
    return _fmt(store.get_model(model_id))


@router.delete("/api/models/{model_id}", status_code=204)
async def delete_model(model_id: int, user_id: int = Depends(require_auth)):
    row = store.get_model(model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    if not row["is_downloaded"]:
        raise HTTPException(409, "Model is not downloaded")

    if row["is_active"]:
        if row["type"] == "face":
            registry.swap_face_engine(None)
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
    from app.core.object_engine import ObjectEngine
    return ObjectEngine(model_name, models_dir() / f"{model_name}.pt")


def _delete_files(model_type: str, model_name: str) -> None:
    if model_type == "face":
        path = models_dir() / "models" / model_name
        if path.exists():
            shutil.rmtree(path)
    else:
        path = models_dir() / f"{model_name}.pt"
        if path.exists():
            path.unlink()


def _fmt(row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "embedding_dim": row["embedding_dim"],
        "is_downloaded": bool(row["is_downloaded"]),
        "is_active": bool(row["is_active"]),
    }
