"""Main page routes — dashboard, gallery, enroll, test, review, models, settings."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.core.auth import get_session_env, get_session_user
from app.db import store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def engine_flags() -> dict:
    """Which recognition engines are active (model activated AND engine loaded).
    Drives nav visibility, the Detect/Test mode control, and the readiness banner —
    all rendered server-side so there's no flash. Shared by every page context."""
    from app.core.engine_registry import registry
    face_active = bool(store.get_active_model("face") and registry.get_face_engine())
    object_active = bool(store.get_active_model("object") and registry.get_object_engine())
    return {"face_active": face_active, "object_active": object_active}

SLIDER_RANGES = {
    "face.match_threshold":        (0.0, 1.0, 0.01),
    "face.detection_confidence":   (0.0, 1.0, 0.01),
    "object.detection_confidence": (0.0, 1.0, 0.01),
    "object.iou_threshold":        (0.0, 1.0, 0.01),
    "system.crop_padding":         (0.0, 0.5, 0.01),
}

# Settings rendered as a dropdown: key -> [(value, label), ...]
SETTING_CHOICES = {
    "face.match_strategy": [
        ("best", "Best matching photo (default)"),
        ("average", "Average all reference photos"),
    ],
}

COCO_CLASSES = [
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
    "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
    "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
    "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
    "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
    "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
    "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
    "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
    "remote","keyboard","cell phone","microwave","oven","toaster","sink",
    "refrigerator","book","clock","vase","scissors","teddy bear","hair drier",
    "toothbrush",
]


def _col(row, key: str, default: str) -> str:
    """Safe column access for sqlite3.Row — avoids IndexError on newly-migrated columns."""
    try:
        return row[key] or default
    except (IndexError, KeyError):
        return default


def _base(request: Request, active: str = "", show_env_switcher: bool = True) -> dict | None:
    """Return base template context, or None if the user is not authenticated."""
    user_id = get_session_user(request)
    if not user_id:
        return None
    user = store.get_user_by_id(user_id)
    if user is None:
        # Stale session — the cookie points to a user that no longer exists
        # (e.g. a fresh/reset DB). Treat as unauthenticated and clear the session.
        request.session.clear()
        return None
    env_id = get_session_env(request)
    if not env_id:
        env_id = store.get_last_environment_id(user_id) or store.get_default_environment_id(user_id)
        if env_id:
            request.session["environment_id"] = env_id
    environments = store.list_environments(user_id)
    env_name = next((e["name"] for e in environments if e["id"] == env_id), "default")
    return {
        "username": request.session.get("username", ""),
        "user_id": user_id,
        "is_admin": bool(user and _col(user, "is_admin", "")),
        "active": active,
        "user_tz": _col(user, "timezone", "UTC"),
        "user_locale": _col(user, "locale", "en-US"),
        "version": __version__,
        "environment_id": env_id,
        "environment_name": env_name,
        "environments": [dict(e) for e in environments],
        "show_env_switcher": show_env_switcher,
        **engine_flags(),
    }


def _r(request: Request, name: str, ctx: dict, **kw):
    return templates.TemplateResponse(request, name, ctx, **kw)


@router.get("/")
async def dashboard(request: Request):
    ctx = _base(request, "dashboard")
    if not ctx:
        return RedirectResponse("/login" if store.count_users() else "/signup")
    return _r(request, "dashboard.html", ctx)


@router.get("/identities/{identity_id}")
async def gallery(identity_id: int, request: Request):
    ctx = _base(request, "dashboard")
    if not ctx:
        return RedirectResponse("/login")
    env_id = ctx["environment_id"]
    identity = store.get_identity_with_counts(identity_id, ctx["user_id"], env_id)
    if not identity:
        return _r(request, "dashboard.html", {**ctx, "error": "Identity not found"}, status_code=404)
    ctx["identity"] = identity
    cover_id = identity["cover_detection_id"]
    if cover_id is None:
        cover_id = store.get_oldest_detection_id(identity_id, ctx["user_id"], env_id)
    ctx["effective_cover_id"] = cover_id
    return _r(request, "gallery.html", ctx)


@router.post("/switch-environment/{env_id}")
async def switch_environment(env_id: int, request: Request):
    ctx = _base(request)
    if not ctx:
        return RedirectResponse("/login")
    env = store.get_environment(env_id, ctx["user_id"])
    if env:
        request.session["environment_id"] = env_id
        store.save_last_environment(ctx["user_id"], env_id)
    referer = request.headers.get("referer", "/")
    return RedirectResponse(referer, status_code=303)


@router.get("/environments")
async def environments_page(request: Request):
    ctx = _base(request, "environments")
    if not ctx:
        return RedirectResponse("/login")
    envs = store.list_environments(ctx["user_id"])
    ctx["env_list"] = [
        {**dict(e), **store.get_environment_stats(e["id"], ctx["user_id"])}
        for e in envs
    ]
    return _r(request, "environments.html", ctx)


@router.get("/enroll")
async def enroll_page(request: Request):
    ctx = _base(request, "enroll")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "enroll.html", ctx)


@router.get("/review")
async def review_page(request: Request):
    ctx = _base(request, "review")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "review.html", ctx)


@router.get("/images")
async def images_page(request: Request):
    ctx = _base(request, "images")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "images.html", ctx)


@router.get("/test")
async def test_page(request: Request):
    ctx = _base(request, "test")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "test.html", ctx)


@router.get("/clusters")
async def clusters_page(request: Request):
    ctx = _base(request, "clusters")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "clusters.html", ctx)


@router.get("/unknown-people")
async def unknown_people_page(request: Request):
    ctx = _base(request, "unknown_people")
    if not ctx:
        return RedirectResponse("/login")
    return _r(request, "unknown_people.html", ctx)


@router.get("/models")
async def models_page(request: Request):
    ctx = _base(request, "models", show_env_switcher=False)
    if not ctx:
        return RedirectResponse("/login")
    if not ctx["is_admin"]:
        return RedirectResponse("/")
    from app.api.models import downloading_ids
    ctx["models"] = [dict(r) for r in store.list_models()]
    # Face model display order: buffalo_l first, antelopev2 last; the rest keep
    # their store (type, name) ordering. Objects are unaffected.
    _face_rank = {"buffalo_l": 0, "antelopev2": 2}
    ctx["models"].sort(key=lambda m: (
        m["type"],
        _face_rank.get(m["name"], 1) if m["type"] == "face" else 0,
        m["name"],
    ))
    ctx["downloading_ids"] = downloading_ids()
    # Object detection scope (COCO class selection / YOLO-World vocabulary) is
    # configured here because it is about what the active object model detects.
    obj_rows = [dict(r) for r in store.get_all_settings() if r["category"] == "object"]
    active_obj = store.get_active_model("object")
    active_obj_name = active_obj["name"] if active_obj else None
    ctx["settings"]         = {"object": obj_rows}
    ctx["coco_classes"]     = COCO_CLASSES
    # Surface the class/vocab editors only when an object engine is actually
    # loaded — the single source of truth for "a detector is live". This covers
    # nothing-active, a deactivated model, and a model flagged active whose
    # weights are gone or failed to load on startup.
    from app.core.engine_registry import registry
    ctx["obj_active"]       = registry.get_object_engine() is not None
    ctx["active_obj_world"] = bool(active_obj_name and "world" in active_obj_name.lower())
    # Florence-2 is open-vocabulary but not user-promptable: no COCO grid, no
    # world vocabulary editor — there is nothing to configure about its classes.
    ctx["active_obj_florence"] = bool(active_obj_name and active_obj_name.lower().startswith("florence"))
    return _r(request, "models.html", ctx)


@router.get("/docs")
async def api_docs(request: Request):
    ctx = _base(request, "docs")
    if ctx:
        return _r(request, "api_docs.html", ctx)
    # Not signed in — render without nav
    return templates.TemplateResponse(request, "api_docs_public.html", {})


@router.get("/settings")
async def settings_page(request: Request):
    ctx = _base(request, "settings", show_env_switcher=False)
    if not ctx:
        return RedirectResponse("/login")
    if not ctx["is_admin"]:
        return RedirectResponse("/")
    rows = store.get_all_settings()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(dict(r))
    try:
        import onnxruntime as ort
        gpu_available = "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        gpu_available = False
    ctx["settings"]        = grouped
    ctx["slider_ranges"]   = SLIDER_RANGES
    ctx["setting_choices"] = SETTING_CHOICES
    ctx["gpu_available"]   = gpu_available
    ctx["managed_users"]   = [dict(u) for u in store.list_managed_users(ctx["user_id"])]
    return _r(request, "settings.html", ctx)


# ---------------------------------------------------------------------------
# Environment page actions (create / rename / delete)
# ---------------------------------------------------------------------------

@router.post("/environments/create")
async def environment_create(request: Request, name: str = Form(...)):
    ctx = _base(request)
    if not ctx:
        return RedirectResponse("/login")
    name = name.strip()
    if name:
        try:
            store.create_environment(ctx["user_id"], name)
        except Exception:
            pass
    # Back to wherever the action came from (modal on any page, or the /environments page).
    return RedirectResponse(request.headers.get("referer", "/environments"), status_code=303)


@router.post("/environments/{env_id}/rename")
async def environment_rename(env_id: int, request: Request, name: str = Form(...)):
    ctx = _base(request)
    if not ctx:
        return RedirectResponse("/login")
    name = name.strip()
    if name:
        try:
            store.rename_environment(env_id, ctx["user_id"], name)
        except Exception:
            pass
    return RedirectResponse(request.headers.get("referer", "/environments"), status_code=303)


@router.post("/environments/{env_id}/delete")
async def environment_delete(env_id: int, request: Request):
    from app.core import face_index as _fi
    from app.core.paths import crops_dir
    ctx = _base(request)
    if not ctx:
        return RedirectResponse("/login")
    envs = store.list_environments(ctx["user_id"])
    if len(envs) > 1:
        deleted, crops = store.delete_environment(env_id, ctx["user_id"])
        if deleted:
            for crop in crops:
                try:
                    (crops_dir() / crop).unlink(missing_ok=True)
                except OSError:
                    pass
            _fi.clear_environment(ctx["user_id"], env_id)
            if request.session.get("environment_id") == env_id:
                remaining = store.list_environments(ctx["user_id"])
                if remaining:
                    request.session["environment_id"] = remaining[0]["id"]
    return RedirectResponse(request.headers.get("referer", "/environments"), status_code=303)
