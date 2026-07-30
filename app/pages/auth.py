"""Sign up, sign in, and sign out page routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import settings_cache
from app.core.auth import get_session_user
from app.core.security import (
    REMEMBER_MAX_AGE,
    create_remember_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    key_hint,
    verify_password,
)
from app.db import store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_COOKIE = "argus_remember"


def _admin_landing() -> str:
    """Where an admin lands after auth: the Models page until at least one model is
    downloaded (so a fresh instance is guided to set one up), then the dashboard."""
    if store.has_downloaded_model("face") or store.has_downloaded_model("object"):
        return "/"
    return "/models"


@router.get("/signup")
async def signup_page(request: Request):
    return templates.TemplateResponse(
        request, "signup.html", {"error": "", "is_first_user": store.count_users() == 0}
    )


@router.post("/signup")
async def signup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str  = Form(...),
):
    username = username.strip()
    err = None
    if not username:
        err = "Username is required."
    elif len(password) < 8:
        err = "Password must be at least 8 characters."
    elif password != confirm:
        err = "Passwords do not match."
    elif store.get_user_by_username(username):
        err = "Username already taken."
    if err:
        return templates.TemplateResponse(
            request, "signup.html", {"error": err, "is_first_user": store.count_users() == 0}
        )

    is_first = store.count_users() == 0
    auto_approve = is_first or settings_cache.cache.get_or("system.auto_approve_users", True)
    user_id = store.create_user(
        username, hash_password(password),
        is_admin=is_first, is_approved=auto_approve,
    )

    if auto_approve:
        env_id = store.get_default_environment_id(user_id)
        plaintext = generate_api_key()
        store.create_api_key(user_id, hash_api_key(plaintext), "Default key", env_id, key_hint(plaintext))
        request.session["user_id"] = user_id
        request.session["username"] = username
        request.session["environment_id"] = env_id
        request.session["new_key"] = plaintext
        # First account is the admin — guide them to set up a model first (the new API
        # key stays in the session and is shown next time they open the Account page).
        dest = _admin_landing() if is_first else "/account"
        return RedirectResponse(dest, status_code=303)

    # Admin approval required
    return templates.TemplateResponse(request, "pending.html", {"request": request})


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember: str = Form(default=""),
):
    row = store.get_user_by_username(username.strip())
    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password."}
        )

    if not row["is_approved"]:
        return templates.TemplateResponse(request, "pending.html", {"request": request})

    request.session["user_id"] = row["id"]
    request.session["username"] = row["username"]
    env_id = store.get_last_environment_id(row["id"]) or store.get_default_environment_id(row["id"])
    if env_id:
        request.session["environment_id"] = env_id

    # Auto-generate first API key if the user has none yet; redirect to account so they see it
    try:
        if row["must_change_password"]:
            response = RedirectResponse("/reset-password", status_code=303)
            if remember:
                secret = os.environ.get("SECRET_KEY", "change-me")
                token = create_remember_token(row["id"], secret)
                response.set_cookie(_COOKIE, token, max_age=REMEMBER_MAX_AGE, httponly=True, samesite="lax")
            return response
    except (IndexError, KeyError):
        pass

    if not store.list_api_keys(row["id"]):
        plaintext = generate_api_key()
        store.create_api_key(row["id"], hash_api_key(plaintext), "Default key", env_id, key_hint(plaintext))
        request.session["new_key"] = plaintext
        redirect_to = "/account"
    elif row["is_admin"]:
        # Admins land on Models until a model is downloaded, then the dashboard.
        redirect_to = _admin_landing()
    else:
        redirect_to = "/"

    response = RedirectResponse(redirect_to, status_code=303)

    if remember:
        secret = os.environ.get("SECRET_KEY", "change-me")
        token = create_remember_token(row["id"], secret)
        response.set_cookie(_COOKIE, token, max_age=REMEMBER_MAX_AGE, httponly=True, samesite="lax")

    return response


@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": False, "error": ""})


@router.post("/forgot-password")
async def forgot_password(request: Request, username: str = Form(...)):
    store.request_password_reset(username.strip())
    # Always show the same confirmation — don't reveal whether the username exists.
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": True, "error": ""})


@router.get("/reset-password")
async def reset_password_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")
    user = store.get_user_by_id(user_id)
    if not user:
        return RedirectResponse("/login")
    try:
        if not user["must_change_password"]:
            return RedirectResponse("/")
    except (IndexError, KeyError):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "reset_password.html", {"error": ""})


@router.post("/reset-password")
async def reset_password(
    request: Request,
    new_password: str = Form(...),
    confirm: str = Form(...),
):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")
    user = store.get_user_by_id(user_id)
    if not user:
        return RedirectResponse("/login")
    try:
        if not user["must_change_password"]:
            return RedirectResponse("/")
    except (IndexError, KeyError):
        return RedirectResponse("/")

    if len(new_password) < 8:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"error": "Password must be at least 8 characters."},
        )
    if new_password != confirm:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"error": "Passwords do not match."},
        )

    store.complete_forced_password_change(user_id, hash_password(new_password))
    dest = _admin_landing() if user["is_admin"] else "/"
    return RedirectResponse(dest, status_code=303)


@router.get("/pending")
async def pending_page(request: Request):
    return templates.TemplateResponse(request, "pending.html", {"request": request})


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(_COOKIE)
    return response
