"""Account page — key management, password change, admin approvals."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.core.auth import get_session_user
from app.core.security import generate_api_key, hash_api_key, hash_password, verify_password
from app.db import store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return None, RedirectResponse("/login")
    user = store.get_user_by_id(user_id)
    if not user:
        return None, RedirectResponse("/login")
    return user, None


def _safe_col(row, key: str, default: str) -> str:
    try:
        return row[key] or default
    except (IndexError, KeyError):
        return default


TIMEZONES = [
    "UTC","America/New_York","America/Chicago","America/Denver","America/Los_Angeles",
    "America/Vancouver","America/Toronto","America/Sao_Paulo","America/Mexico_City",
    "Europe/London","Europe/Paris","Europe/Berlin","Europe/Rome","Europe/Madrid",
    "Europe/Amsterdam","Europe/Stockholm","Europe/Zurich","Europe/Moscow",
    "Asia/Tokyo","Asia/Shanghai","Asia/Singapore","Asia/Seoul","Asia/Kolkata",
    "Asia/Dubai","Asia/Bangkok","Australia/Sydney","Australia/Melbourne",
    "Pacific/Auckland","Pacific/Honolulu",
]

LOCALES = [
    ("en-US","English (US)"),("en-GB","English (UK)"),("en-AU","English (AU)"),
    ("fr-FR","Français"),("de-DE","Deutsch"),("es-ES","Español"),("it-IT","Italiano"),
    ("pt-BR","Português (BR)"),("ja-JP","日本語"),("ko-KR","한국어"),
    ("zh-CN","中文 (简体)"),("zh-TW","中文 (繁體)"),
]


def _render(request: Request, user, error: str = "", success: str = ""):
    new_key = request.session.pop("new_key", None)
    managed = store.list_managed_users(user["id"]) if user["is_admin"] else []
    keys = store.list_api_keys(user["id"])
    return templates.TemplateResponse(request, "account.html", {
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "keys": [dict(k) for k in keys],
        "managed_users": [dict(u) for u in managed],
        "new_key": new_key,
        "error": error,
        "success": success,
        "user_tz": _safe_col(user, "timezone", "UTC"),
        "user_locale": _safe_col(user, "locale", "en-US"),
        "version": __version__,
        "timezones": TIMEZONES,
        "locales": LOCALES,
    })


@router.get("/account")
async def account_page(request: Request):
    user, redir = _require(request)
    if redir:
        return redir
    return _render(request, user)


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

@router.post("/account/key/create")
async def create_key(request: Request, label: str = Form(default="")):
    user, redir = _require(request)
    if redir:
        return redir
    plaintext = generate_api_key()
    store.create_api_key(user["id"], hash_api_key(plaintext), label.strip() or "Unnamed key")
    request.session["new_key"] = plaintext
    return RedirectResponse("/account", status_code=303)


@router.post("/account/key/{key_id}/revoke")
async def revoke_key(key_id: int, request: Request):
    user, redir = _require(request)
    if redir:
        return redir
    store.revoke_api_key(key_id, user["id"])
    return RedirectResponse("/account", status_code=303)


@router.post("/account/key/{key_id}/delete")
async def delete_key(key_id: int, request: Request):
    user, redir = _require(request)
    if redir:
        return redir
    store.delete_api_key(key_id, user["id"])
    return RedirectResponse("/account", status_code=303)


@router.post("/account/key/revoke-all")
async def revoke_all_keys(request: Request):
    user, redir = _require(request)
    if redir:
        return redir
    for k in store.list_api_keys(user["id"]):
        store.revoke_api_key(k["id"], user["id"])
    return RedirectResponse("/account", status_code=303)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@router.post("/account/preferences")
async def update_preferences(
    request: Request,
    timezone: str = Form(...),
    locale: str = Form(...),
):
    user, redir = _require(request)
    if redir:
        return redir
    if timezone not in TIMEZONES:
        return _render(request, user, error="Invalid timezone.")
    if locale not in dict(LOCALES):
        return _render(request, user, error="Invalid locale.")
    store.update_user_preferences(user["id"], timezone, locale)
    user = store.get_user_by_id(user["id"])  # re-fetch so window.USER_TZ reflects new value
    return _render(request, user, success="Preferences saved.")


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

@router.post("/account/password")
async def change_password(
    request: Request,
    current: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
):
    user, redir = _require(request)
    if redir:
        return redir

    if not verify_password(current, user["password_hash"]):
        return _render(request, user, error="Current password is incorrect.")
    if len(new_password) < 8:
        return _render(request, user, error="New password must be at least 8 characters.")
    if new_password != confirm:
        return _render(request, user, error="Passwords do not match.")

    store.update_password(user["id"], hash_password(new_password))
    return _render(request, user, success="Password updated.")


# ---------------------------------------------------------------------------
# Self-service — delete own account (non-admin only)
# ---------------------------------------------------------------------------

@router.post("/account/delete")
async def delete_own_account(request: Request):
    user, redir = _require(request)
    if redir:
        return redir
    if user["is_admin"]:
        return _render(request, user, error="The admin account cannot be deleted.")
    store.delete_user(user["id"])
    request.session.clear()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("argus_remember")
    return resp


# ---------------------------------------------------------------------------
# Admin — user management (approve, revoke/grant access, delete)
# ---------------------------------------------------------------------------

def _admin_or_redirect(request: Request):
    """Return (user, None) if the caller is an admin, else (None, redirect)."""
    user, redir = _require(request)
    if redir:
        return None, redir
    if not user["is_admin"]:
        return None, RedirectResponse("/account", status_code=303)
    return user, None


@router.post("/admin/approve/{user_id}")
async def approve_user(user_id: int, request: Request):
    user, redir = _admin_or_redirect(request)
    if redir:
        return redir
    store.set_user_approved(user_id, True)
    return RedirectResponse("/settings", status_code=303)


@router.post("/admin/revoke/{user_id}")
async def revoke_user(user_id: int, request: Request):
    user, redir = _admin_or_redirect(request)
    if redir:
        return redir
    store.set_user_approved(user_id, False)
    return RedirectResponse("/settings", status_code=303)


@router.post("/admin/user/{user_id}/delete")
async def admin_delete_user(user_id: int, request: Request):
    user, redir = _admin_or_redirect(request)
    if redir:
        return redir
    if user_id != user["id"]:  # never delete yourself here
        store.delete_user(user_id)
        from app.core import face_index as _fi
        _fi.rebuild_user(user_id)  # drop their entries from the in-memory index
    return RedirectResponse("/settings", status_code=303)
