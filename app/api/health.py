from __future__ import annotations

import platform

from fastapi import APIRouter

from app import __version__
from app.core import settings_cache
from app.core.engine_registry import registry
from app.db import store


def _cpu_name() -> str:
    try:
        import cpuinfo
        return cpuinfo.get_cpu_info().get("brand_raw", "") or platform.processor()
    except Exception:
        return platform.processor()


def _gpu_name() -> str | None:
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        pynvml.nvmlShutdown()
        return name if isinstance(name, str) else name.decode()
    except Exception:
        return None


def _memory_info() -> dict | None:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "total_gb": round(vm.total / 1024 ** 3, 1),
            "available_gb": round(vm.available / 1024 ** 3, 1),
        }
    except Exception:
        return None


def _os_info() -> dict:
    system = platform.system()
    if system == "Darwin":
        ver, _, _ = platform.mac_ver()
        return {"name": "macOS", "version": ver}
    if system == "Windows":
        return {"name": "Windows", "version": platform.version()}
    return {"name": system, "version": platform.version()}

router = APIRouter()


@router.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        gpu_available = "CUDAExecutionProvider" in providers
        use_gpu = settings_cache.cache.get_or("system.use_gpu", True)
        active_provider = "cuda" if (gpu_available and use_gpu) else "cpu"
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
        use_gpu = settings_cache.cache.get_or("system.use_gpu", True)
        active_provider = "cuda" if (gpu_available and use_gpu) else "cpu"
    except ImportError:
        gpu_available = None
        active_provider = None

    face_row    = store.get_active_model("face")
    object_row  = store.get_active_model("object")
    face_ready  = bool(face_row   and registry.get_face_engine())
    object_ready = bool(object_row and registry.get_object_engine())

    return {
        "version": __version__,
        "os": _os_info(),
        "cpu_name": _cpu_name(),
        "memory": _memory_info(),
        "gpu_available": gpu_available,
        "gpu_name": _gpu_name() if gpu_available else None,
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
            "changes_list_max": 1000,
            "batch_detect_max": 500,
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
