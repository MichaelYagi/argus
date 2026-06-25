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


# Formats Argus can decode (Pillow-detected on input; never trusts extension).
_SUPPORTED_FORMATS = ["JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF", "HEIC", "HEIF", "MPO"]


@router.get("/api/capabilities")
async def capabilities():
    """Discovery manifest so clients can adapt instead of hardcoding assumptions:
    which detection types are usable right now, supported formats, pagination limits,
    and which integration features this build exposes."""
    try:
        import onnxruntime as ort
        gpu_available = "CUDAExecutionProvider" in ort.get_available_providers()
        active_provider = "cuda" if gpu_available else "cpu"
    except ImportError:
        gpu_available = None
        active_provider = None

    face_row    = store.get_active_model("face")
    object_row  = store.get_active_model("object")
    face_ready  = bool(face_row   and registry.get_face_engine())
    object_ready = bool(object_row and registry.get_object_engine())

    return {
        "version": __version__,
        "gpu_available": gpu_available,
        "active_provider": active_provider,
        "detection": {
            "faces":   {"available": face_ready,   "downloaded": store.has_downloaded_model("face"),
                        "active_model": face_row["name"]   if face_row   else None},
            "objects": {"available": object_ready, "downloaded": store.has_downloaded_model("object"),
                        "active_model": object_row["name"] if object_row else None},
        },
        "supported_formats": _SUPPORTED_FORMATS,
        "image_input": ["file", "image_url", "image_base64"],
        "limits": {
            "identities_list_max": 200,
            "identities_summary_max": 1000,
            "changes_max": 1000,
            "batch_max": 500,
        },
        "features": {
            "external_ref": True,
            "change_feed": True,
            "batch_label": True,
            "batch_read": True,
            "stateless_test": True,
            "environments": True,
        },
    }
