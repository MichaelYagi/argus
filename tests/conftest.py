"""
Stub heavy ML dependencies that are absent in the minimal dev install so that
engine modules can be imported and mocked in unit tests.  When the real package
is present (CI / full install) sys.modules.setdefault is a no-op.
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
):
    sys.modules.setdefault(_mod, MagicMock())
