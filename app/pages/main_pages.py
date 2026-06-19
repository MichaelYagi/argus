"""Main page routes — dashboard, gallery, enroll, test, review, models, settings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.auth import get_session_user
from app.db import store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

SLIDER_RANGES = {
    "face.match_threshold":        (0.0, 1.0, 0.01),
    "face.detection_confidence":   (0.0, 1.0, 0.01),
    "object.detection_confidence": (0.0, 1.0, 0.01),
    "object.iou_threshold":        (0.0, 1.0, 0.01),
    "system.crop_padding":         (0.0, 0.5, 0.01),
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


def _base(request: Request, active: str = "") -> dict | None:
    """Return base template context, or None if the user is not authenticated."""
    user_id = get_session_user(request)
    if not user_id:
        return None
    return {
        "username": request.session.get("username", ""),
        "user_id": user_id,
        "active": active,
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
    identity = store.get_identity_with_counts(identity_id, ctx["user_id"])
    if not identity:
        return _r(request, "dashboard.html", {**ctx, "error": "Identity not found"}, status_code=404)
    ctx["identity"] = identity
    return _r(request, "gallery.html", ctx)


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


@router.get("/models")
async def models_page(request: Request):
    ctx = _base(request, "models")
    if not ctx:
        return RedirectResponse("/login")
    ctx["models"] = [dict(r) for r in store.list_models()]
    return _r(request, "models.html", ctx)


@router.get("/settings")
async def settings_page(request: Request):
    ctx = _base(request, "settings")
    if not ctx:
        return RedirectResponse("/login")
    rows = store.get_all_settings()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(dict(r))
    ctx["settings"] = grouped
    ctx["slider_ranges"] = SLIDER_RANGES
    ctx["coco_classes"] = COCO_CLASSES
    return _r(request, "settings.html", ctx)
