"""
Stub heavy ML / image-processing dependencies that are absent in the minimal
dev install so that modules can be imported and mocked in unit tests.
When the real package is present (CI / full install), setdefault is a no-op.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod in (
    "numpy",
    "insightface",
    "insightface.app",
    "ultralytics",
    "onnxruntime",
    "torch",
    "PIL",
    "PIL.Image",
    "httpx",
    "pillow_heif",
    "faiss",
    "faiss.swigfaiss",
):
    sys.modules.setdefault(_mod, MagicMock())


@pytest.fixture(autouse=True)
def _no_fairface_engine():
    """Prevent the on-disk FairFace model from interfering with unit tests.

    The app may load FairFace from disk at startup when the model file exists.
    Tests that mock to_rgb_array (returning MagicMock) would crash inside the
    real FairFace inference path. Suppress the engine globally; tests that
    specifically need FairFace behaviour can override with their own patch.
    """
    from app.inference.registry import registry
    with patch.object(registry, "get_fairface_engine", return_value=None):
        yield
