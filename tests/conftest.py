"""
Stub heavy ML / image-processing dependencies that are absent in the minimal
dev install so that modules can be imported and mocked in unit tests.
When the real package is present (CI / full install), setdefault is a no-op.
"""

import sys
from unittest.mock import MagicMock

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
):
    sys.modules.setdefault(_mod, MagicMock())
