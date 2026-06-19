"""Auth dependencies for API routes and page routes."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request, Security
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


def get_session_user(request: Request) -> int | None:
    """Return user_id from session or remember-me cookie, or None."""
    uid = _user_from_session(request)
    if uid:
        return uid
    token = request.cookies.get("argus_remember")
    if token:
        secret = os.environ.get("SECRET_KEY", "change-me")
        uid = verify_remember_token(token, secret)
        if uid:
            request.session["user_id"] = uid
            return uid
    return None


def _user_from_session(request: Request) -> int | None:
    uid = request.session.get("user_id")
    return int(uid) if uid else None
