from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.core.auth import require_api_key
from app.core.engine_registry import registry

router = APIRouter()


@router.get("/api/health")
async def health(_: str = Depends(require_api_key)):
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        gpu_available = "CUDAExecutionProvider" in providers
        active_provider = "cuda" if gpu_available else "cpu"
    except ImportError:
        gpu_available = None
        active_provider = None

    return {
        "status": "ok",
        "version": __version__,
        "gpu_available": gpu_available,
        "active_provider": active_provider,
        "face_engine": type(registry.get_face_engine()).__name__ if registry.get_face_engine() else None,
        "object_engine": type(registry.get_object_engine()).__name__ if registry.get_object_engine() else None,
    }
