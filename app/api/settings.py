"""Settings endpoints — GET/PUT/reset. All changes are live (no restart needed)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core import settings_cache
from app.core.auth import require_admin
from app.db import store

router = APIRouter()

_VALID_TYPES = {"float", "int", "bool", "string"}
_VALID_CATEGORIES = {"face", "object", "system", "keywords"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/settings")
async def list_settings(user_id: int = Depends(require_admin)):
    """All settings grouped by category. Values are type-coerced."""
    rows = store.get_all_settings()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(_fmt(r))
    return grouped


@router.get("/api/settings/{key:path}")
async def get_setting(key: str, user_id: int = Depends(require_admin)):
    row = store.get_setting(key)
    if not row:
        raise HTTPException(404, f"Setting '{key}' not found")
    return _fmt(row)


@router.put("/api/settings/{key:path}")
async def update_setting(key: str, body: _UpdateBody, user_id: int = Depends(require_admin)):
    row = store.get_setting(key)
    if not row:
        raise HTTPException(404, f"Setting '{key}' not found")

    value_str = _validate_value(key, str(body.value), row["value_type"])

    store.update_setting(key, value_str)
    settings_cache.cache.set(key, value_str, row["value_type"])

    # Changing how faces are matched requires rebuilding the index (centroid vs
    # per-reference vectors).
    if key == "face.match_strategy":
        model_row = store.get_active_model("face")
        if model_row:
            from app.core import face_index
            face_index.build_all(model_row["id"])

    # Resize the live log ring buffer when its size setting changes.
    if key == "system.log_buffer_size":
        from app.core import log_buffer
        log_buffer.resize(int(value_str))

    # Changing the prompt template re-renders every vocabulary word, so bump the
    # vocab version (new text-matrix cache key) and re-tag the library.
    if key == "clip.prompt_template":
        store.bump_vocab_version()
        from app.core import keyword_jobs
        keyword_jobs.trigger_vocab_change()

    return _fmt(store.get_setting(key))


@router.post("/api/settings/reset")
async def reset_settings(
    key: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user_id: int = Depends(require_admin),
):
    """Reset one key (?key=…) or an entire category (?category=…) to defaults."""
    if key and category:
        raise HTTPException(400, "Provide ?key or ?category, not both")
    if not key and not category:
        raise HTTPException(400, "Provide ?key=… or ?category=…")

    defaults = store.get_settings_defaults()

    if key:
        if key not in defaults:
            raise HTTPException(404, f"Setting '{key}' not found")
        _apply_reset(key, defaults[key])
        return [_fmt(store.get_setting(key))]

    if category not in _VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(_VALID_CATEGORIES)}")

    reset_keys = [k for k in defaults if k.startswith(f"{category}.")]
    for k in reset_keys:
        _apply_reset(k, defaults[k])
    return [_fmt(store.get_setting(k)) for k in reset_keys]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _UpdateBody(BaseModel):
    value: object


def _validate_value(key: str, raw: str, value_type: str) -> str:
    """Coerce and validate raw string against value_type. Returns normalised string."""
    raw = raw.strip()
    try:
        if value_type == "float":
            float(raw)
        elif value_type == "int":
            int(raw)
        elif value_type == "bool":
            if raw.lower() not in ("true", "false"):
                raise ValueError
            raw = raw.lower()
        # string: any value is fine
    except ValueError:
        raise HTTPException(400, f"Invalid value '{raw}' for type '{value_type}'")

    # Special rule: system.use_gpu = true is rejected when GPU is unavailable
    if key == "system.use_gpu" and raw == "true":
        _require_gpu_available()

    # Log buffer size must stay within sane memory bounds.
    if key == "system.log_buffer_size":
        from app.core import log_buffer
        if not (log_buffer.MIN_SIZE <= int(raw) <= log_buffer.MAX_SIZE):
            raise HTTPException(
                400,
                f"system.log_buffer_size must be between {log_buffer.MIN_SIZE} and {log_buffer.MAX_SIZE}",
            )

    # Keyword tagging bounds.
    if key == "clip.tag_top_k" and not (1 <= int(raw) <= 100):
        raise HTTPException(400, "clip.tag_top_k must be between 1 and 100")
    if key == "clip.tag_threshold" and not (0.0 <= float(raw) <= 1.0):
        raise HTTPException(400, "clip.tag_threshold must be between 0 and 1")
    if key == "clip.tag_diversity" and not (0.0 <= float(raw) <= 1.0):
        raise HTTPException(400, "clip.tag_diversity must be between 0 and 1")
    if key == "clip.tag_rel_floor" and not (0.0 <= float(raw) <= 1.0):
        raise HTTPException(400, "clip.tag_rel_floor must be between 0 and 1")
    if key == "clip.prompt_template" and "{word}" not in raw:
        raise HTTPException(400, "clip.prompt_template must contain {word}")

    # Face matching method is a fixed choice.
    if key == "face.match_strategy":
        if raw.lower() not in ("average", "best"):
            raise HTTPException(400, "face.match_strategy must be 'average' or 'best'")
        raw = raw.lower()

    return raw


def _require_gpu_available() -> None:
    from app.core import accelerator
    if accelerator.gpu_available():
        return
    raise HTTPException(400, "GPU is not available on this system. Cannot enable system.use_gpu.")


def _apply_reset(key: str, default_value: str) -> None:
    row = store.get_setting(key)
    if row:
        store.update_setting(key, default_value)
        settings_cache.cache.set(key, default_value, row["value_type"])


def _fmt(row) -> dict:
    from app.core.settings_cache import _coerce
    return {
        "key": row["key"],
        "value": _coerce(row["value"], row["value_type"]),
        "value_type": row["value_type"],
        "category": row["category"],
        "description": row["description"],
        "updated_at": row["updated_at"],
    }
