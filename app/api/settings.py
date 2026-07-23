"""Settings endpoints — GET/PUT/reset. All changes are live (no restart needed)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api._responses import ERR_400, ERR_401, ERR_404, ok
from app.core import settings_cache
from app.core.auth import require_admin
from app.db import store

router = APIRouter()

_VALID_TYPES = {"float", "int", "bool", "string"}
_VALID_CATEGORIES = {"face", "object", "system"}

# (min, max) — None means no bound on that side.
_RANGE_CONSTRAINTS: dict[str, tuple[float | None, float | None]] = {
    "face.match_threshold":        (0.0, 1.0),
    "face.auto_confirm_threshold": (0.0, 1.0),
    "face.auto_enroll_threshold":  (0.0, 1.0),
    "face.detection_confidence":   (0.0, 1.0),
    "face.cluster_threshold":      (0.0, 1.0),
    "face.min_face_size":          (0, None),
    "object.detection_confidence": (0.0, 1.0),
    "object.iou_threshold":        (0.0, 1.0),
    "system.gallery_page_size":    (1, None),
    "system.crop_padding":         (0.0, None),
    "system.url_fetch_timeout_seconds": (1, None),
    "system.url_fetch_max_size_mb":     (1, None),
    "system.ingest_jpeg_quality":  (1, 95),
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_SETTING_EXAMPLE = {
    "key": "face.match_threshold",
    "value": 0.5,
    "value_type": "float",
    "category": "face",
    "description": "Minimum cosine similarity to accept a face match",
    "updated_at": "2026-01-01T00:00:00Z",
}


@router.get(
    "/api/settings",
    responses={
        **ok({
            "face": [_SETTING_EXAMPLE],
            "object": [
                {
                    "key": "object.detection_confidence",
                    "value": 0.4,
                    "value_type": "float",
                    "category": "object",
                    "description": "Minimum confidence for object detections",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            "system": [],
        }),
        **ERR_401,
    },
)
async def list_settings(user_id: int = Depends(require_admin)):
    """All settings grouped by category. Values are type-coerced."""
    rows = store.get_all_settings()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(_fmt(r))
    return grouped


@router.get(
    "/api/settings/{key:path}",
    responses={**ok(_SETTING_EXAMPLE), **ERR_401, **ERR_404},
)
async def get_setting(key: str, user_id: int = Depends(require_admin)):
    row = store.get_setting(key)
    if not row:
        raise HTTPException(404, f"Setting '{key}' not found")
    return _fmt(row)


@router.put(
    "/api/settings/{key:path}",
    responses={**ok({**_SETTING_EXAMPLE, "value": 0.6}), **ERR_401, **ERR_404, **ERR_400},
)
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
        from app.core import activity_buffer, log_buffer
        log_buffer.resize(int(value_str))
        activity_buffer.resize(int(value_str))

    from app.core import activity_buffer as _ab
    _ab.emit("settings", f"Setting changed: {key} → {value_str}")

    return _fmt(store.get_setting(key))


@router.post(
    "/api/settings/reset",
    responses={**ok([_SETTING_EXAMPLE]), **ERR_401, **ERR_404, **ERR_400},
)
async def reset_settings(
    key: str | None = Query(None),
    category: str | None = Query(None),
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

    # Face matching method is a fixed choice.
    if key == "face.match_strategy":
        if raw.lower() not in ("average", "best", "topk_weighted"):
            raise HTTPException(400, "face.match_strategy must be 'best', 'average', or 'topk_weighted'")
        raw = raw.lower()

    # Numeric range constraints.
    if key in _RANGE_CONSTRAINTS and value_type in ("float", "int"):
        num = float(raw) if value_type == "float" else int(raw)
        lo, hi = _RANGE_CONSTRAINTS[key]
        if (lo is not None and num < lo) or (hi is not None and num > hi):
            parts = []
            if lo is not None:
                parts.append(f">= {lo}")
            if hi is not None:
                parts.append(f"<= {hi}")
            raise HTTPException(400, f"'{key}' must be {' and '.join(parts)}, got {raw}")

    return raw


def _require_gpu_available() -> None:
    try:
        import onnxruntime as ort
        if "CUDAExecutionProvider" in ort.get_available_providers():
            return
    except ImportError:
        pass
    raise HTTPException(400, "GPU is not available on this system. Cannot enable system.use_gpu.")


def _apply_reset(key: str, default_value: str) -> None:
    row = store.get_setting(key)
    if not row:
        return
    store.update_setting(key, default_value)
    settings_cache.cache.set(key, default_value, row["value_type"])

    if key == "face.match_strategy":
        model_row = store.get_active_model("face")
        if model_row:
            from app.core import face_index
            face_index.build_all(model_row["id"])

    if key == "system.log_buffer_size":
        from app.core import activity_buffer, log_buffer
        log_buffer.resize(int(default_value))
        activity_buffer.resize(int(default_value))


def _fmt(row) -> dict:
    from app.core.settings_cache import coerce_setting
    return {
        "key": row["key"],
        "value": coerce_setting(row["value"], row["value_type"]),
        "value_type": row["value_type"],
        "category": row["category"],
        "description": row["description"],
        "updated_at": row["updated_at"],
    }
