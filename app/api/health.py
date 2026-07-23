from __future__ import annotations

import platform

from fastapi import APIRouter

from app import __version__
from app.api._responses import ERR_400, ok
from app.core import settings_cache
from app.db import store
from app.inference.registry import registry
from app.inference.runner import _inference_url


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

def _sidecar_model_status(url: str) -> dict:
    """Query the inference sidecar's health endpoint; return model names, None on failure, and reachability."""
    import httpx

    try:
        resp = httpx.get(f"{url}/infer/health", timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "face_model": data.get("face_model"),
                "object_model": data.get("object_model"),
                "sidecar_reachable": True,
            }
    except Exception:
        pass
    return {"face_model": None, "object_model": None, "sidecar_reachable": False}


router = APIRouter()


@router.api_route(
    "/api/health",
    methods=["GET", "HEAD"],
    responses={
        **ok({
            "status": "ok",
            "version": "0.1.0-alpha.20",
            "gpu_available": True,
            "active_provider": "cuda",
            "face_model": "buffalo_l",
            "object_model": "yolov8s",
        }),
    },
)
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

    sidecar_url = _inference_url()
    if sidecar_url:
        model_status = _sidecar_model_status(sidecar_url)
    else:
        face_row   = store.get_active_model("face")
        object_row = store.get_active_model("object")
        model_status = {
            "face_model":   face_row["name"]   if face_row   and registry.get_face_engine()   else None,
            "object_model": object_row["name"] if object_row and registry.get_object_engine() else None,
        }

    return {
        "status": "ok",
        "version": __version__,
        "gpu_available": gpu_available,
        "active_provider": active_provider,
        **model_status,
    }


@router.get(
    "/api/storage",
    responses={**ok({"data_path": "/data", "storage_bytes": 2147483648, "storage": "2.0 GB"})},
)
async def storage():
    """Size of the Argus data directory in bytes."""
    from app.api.identities import _cached_storage
    from app.core.paths import data_dir
    used_str, _, used_bytes, _ = await _cached_storage()
    return {"data_path": str(data_dir()), "storage_bytes": used_bytes, "storage": used_str}


# Formats Argus can decode (Pillow-detected on input; never trusts extension).
_SUPPORTED_FORMATS = ["JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF", "HEIC", "HEIF", "AVIF", "MPO"]


@router.get(
    "/api/capabilities",
    responses={
        **ok({
            "version": "0.1.0-alpha.20",
            "os": {"name": "Linux", "version": "6.1.0"},
            "cpu_name": "Intel Core i7-12700K",
            "memory": {"total_gb": 32.0, "available_gb": 18.4},
            "gpu_available": True,
            "gpu_name": "NVIDIA GeForce RTX 3080",
            "active_provider": "cuda",
            "detection": {
                "faces": {"available": True, "downloaded": True, "active_model": "buffalo_l"},
                "objects": {"available": True, "downloaded": True, "active_model": "yolov8s"},
            },
            "supported_formats": ["JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF", "HEIC", "HEIF", "AVIF", "MPO"],
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
        }),
        **ERR_400,
    },
)
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
    sidecar_url = _inference_url()
    if sidecar_url:
        status = _sidecar_model_status(sidecar_url)
        face_ready   = status["face_model"] is not None
        object_ready = status["object_model"] is not None
    else:
        face_ready   = bool(face_row   and registry.get_face_engine())
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
