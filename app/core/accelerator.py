"""Capability-based accelerator selection, shared by every engine.

There is no per-OS branching anywhere here. Each inference runtime is asked what it
actually supports, and the best option is chosen against a fixed preference order, so
the same code does the right thing on any platform:

  - NVIDIA CUDA on PC / Linux with an NVIDIA GPU
  - Apple CoreML (onnxruntime) and MPS (torch) on Apple Silicon
  - CPU everywhere else, or when system.use_gpu is off

Two runtimes, two queries:
  - onnxruntime (faces via InsightFace, CLIP)  -> execution providers
  - torch (objects via Ultralytics YOLO)       -> device string
"""
from __future__ import annotations

from app.core import settings_cache

# onnxruntime GPU providers we know how to use, best first. CPU is the always-present
# fallback appended after whichever (if any) of these is available.
_ONNX_GPU_PROVIDERS = ("CUDAExecutionProvider", "CoreMLExecutionProvider")

_PROVIDER_LABELS = {
    "CUDAExecutionProvider": "cuda",
    "CoreMLExecutionProvider": "coreml",
    "CPUExecutionProvider": "cpu",
}


def _use_gpu() -> bool:
    return bool(settings_cache.cache.get_or("system.use_gpu", True))


def onnx_available_providers() -> list[str]:
    """Providers onnxruntime reports, or [] if onnxruntime can't be imported."""
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return []


def _torch_cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _torch_mps() -> bool:
    try:
        import torch
        return bool(torch.backends.mps.is_available())
    except Exception:
        return False


def select_providers() -> list[str]:
    """onnxruntime providers for a session: best available accelerator + CPU fallback,
    honoring system.use_gpu. CPU-only when GPU is disabled or unavailable."""
    if _use_gpu():
        available = onnx_available_providers()
        for p in _ONNX_GPU_PROVIDERS:
            if p in available:
                return [p, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def select_device() -> str:
    """torch device string for Ultralytics: 'cuda:0', 'mps', or 'cpu', honoring
    system.use_gpu and actual availability."""
    if _use_gpu():
        if _torch_cuda():
            return "cuda:0"
        if _torch_mps():
            return "mps"
    return "cpu"


def face_runtime() -> tuple[list[str], int]:
    """(providers, ctx_id) for InsightFace. InsightFace's ctx_id is CUDA-centric, so
    faces accelerate on CUDA only and otherwise run on CPU; CoreML face acceleration is
    unvalidated and intentionally left out for now. CLIP, which we control directly,
    still uses the full select_providers() preference (incl. CoreML)."""
    if _use_gpu() and "CUDAExecutionProvider" in onnx_available_providers():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"], 0
    return ["CPUExecutionProvider"], -1


def gpu_available() -> bool:
    """True when any non-CPU accelerator exists on either runtime, regardless of the
    use_gpu setting. Drives the settings toggle and the use_gpu validation: on an Apple
    machine this is true via MPS/CoreML even though there's no CUDA."""
    if any(p in onnx_available_providers() for p in _ONNX_GPU_PROVIDERS):
        return True
    return _torch_cuda() or _torch_mps()


def active_provider() -> str | None:
    """Short label of the onnxruntime accelerator currently in effect ('cuda', 'coreml',
    'cpu'), for health. None when onnxruntime isn't importable."""
    if not onnx_available_providers():
        return None
    return _PROVIDER_LABELS.get(select_providers()[0], select_providers()[0].lower())
