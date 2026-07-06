"""Shared torch device selection for all engine types."""

from __future__ import annotations

from app.core import settings_cache


def torch_device(mps: bool = True) -> str:
    """Return a torch device string honoring system.use_gpu.

    mps=True  — include Apple Silicon MPS as a fallback before CPU.
    mps=False — CUDA or CPU only (YOLO has no MPS code path).
    """
    if not settings_cache.cache.get_or("system.use_gpu", True):
        return "cpu"
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda:0"
    if mps:
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return "mps"
    return "cpu"
