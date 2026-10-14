"""Centralised path helpers — everything reads DATA_PATH from env."""

from __future__ import annotations

import os
import sys
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


def templates_dir() -> Path:
    """Absolute path to the Jinja2 templates directory.

    In a PyInstaller frozen app the working directory is not _MEIPASS, so a
    relative 'app/templates' path won't resolve.  Use _MEIPASS when frozen,
    __file__-relative path otherwise (works for both CLI and dev server).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "templates"
    return Path(__file__).parent.parent / "templates"
