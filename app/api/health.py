from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.engine_registry import registry
from app.db import store

router = APIRouter()


@router.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        gpu_available = "CUDAExecutionProvider" in providers
        active_provider = "cuda" if gpu_available else "cpu"
    except ImportError:
        gpu_available = None
        active_provider = None

    face_row   = store.get_active_model("face")
    object_row = store.get_active_model("object")

    return {
        "status": "ok",
        "version": __version__,
        "gpu_available": gpu_available,
        "active_provider": active_provider,
        "face_model":   face_row["name"]   if face_row   and registry.get_face_engine()   else None,
        "object_model": object_row["name"] if object_row and registry.get_object_engine() else None,
    }
