"""Centralised path helpers — everything reads DATA_PATH from env."""

from __future__ import annotations

import os
from pathlib import Path


def models_dir() -> Path:
    return Path(os.environ.get("MODELS_PATH", "models"))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_PATH", "data"))


def sources_dir() -> Path:
    return data_dir() / "sources"


def crops_dir() -> Path:
    return data_dir() / "crops"


def logs_dir() -> Path:
    return Path(os.environ.get("LOG_PATH", "logs"))
