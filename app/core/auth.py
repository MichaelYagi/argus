"""Auth dependencies for API routes and page routes."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.security import hash_api_key, verify_remember_token
from app.db import store

_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_auth(
    request: Request,
    api_key: Optional[str] = Security(_scheme),
) -> int:
    """Validate X-API-Key from DB, or fall back to session. Returns user_id.

    Used on all /api/* routes — accepts either a DB-issued API key or a valid
    browser session, so the web UI can call its own API without a key header.
    """
    if api_key:
        key_hash = hash_api_key(api_key)
        row = store.get_api_key_user(key_hash)
        if row:
            store.touch_api_key(row["key_id"])
            return int(row["user_id"])
        raise HTTPException(403, "Invalid API key")

    user_id = _user_from_session(request)
    if user_id:
        return user_id

    raise HTTPException(401, "Provide an X-API-Key header or log in.")


async def require_admin(user_id: int = Depends(require_auth)) -> int:
    """Like require_auth, but also requires the user to be an admin. Used to gate
    instance-global resources (settings, models) that affect every account."""
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    return user_id


def is_admin(user_id: int | None) -> bool:
    """True if the user exists and is an admin (the first registered account)."""
    if not user_id:
        return False
    user = store.get_user_by_id(user_id)
    return bool(user and user["is_admin"])


def get_session_user(request: Request) -> int | None:
    """Return user_id from session or remember-me cookie, or None.

    Always ensures 'username' is in the session so every page using
    base.html renders the nav account link correctly.
    """
    uid = _user_from_session(request)
    if uid:
        _restore_username(request, uid)
        return uid

    token = request.cookies.get("argus_remember")
    if token:
        secret = os.environ.get("SECRET_KEY", "change-me")
        uid = verify_remember_token(token, secret)
        if uid:
            request.session["user_id"] = uid
            _restore_username(request, uid)
            return uid

    return None


def _restore_username(request: Request, uid: int) -> None:
    """Set session username from DB if it is missing."""
    if not request.session.get("username"):
        user = store.get_user_by_id(uid)
        if user:
            request.session["username"] = user["username"]


def _user_from_session(request: Request) -> int | None:
    uid = request.session.get("user_id")
    return int(uid) if uid else None
