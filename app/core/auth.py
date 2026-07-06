"""Auth dependencies for API routes and page routes."""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.security import hash_api_key, verify_remember_token
from app.db import store

_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _resolve_api_key(
    request: Request,
    api_key: str | None = Security(_scheme),
) -> store.Row | None:
    """Return the api_keys row for the given key, or None for session-auth requests."""
    if api_key:
        key_hash = hash_api_key(api_key)
        row = store.get_api_key_user(key_hash)
        if row:
            store.touch_api_key(row["key_id"])
            return row
        raise HTTPException(403, "Invalid API key")
    return None


async def require_auth(
    request: Request,
    key_row: store.Row | None = Depends(_resolve_api_key),
) -> int:
    """Returns user_id. Accepts API key or browser session."""
    if key_row:
        return int(key_row["user_id"])
    user_id = _user_from_session(request)
    if user_id:
        return user_id
    raise HTTPException(401, "Provide an X-API-Key header or log in.")


async def require_env_id(
    request: Request,
    key_row: store.Row | None = Depends(_resolve_api_key),
) -> int:
    """Returns environment_id. API key -> key's environment. Browser session -> session env."""
    if key_row:
        env_id = key_row["environment_id"]
        if not env_id:
            raise HTTPException(500, "API key has no environment assigned")
        return int(env_id)
    user_id = _user_from_session(request)
    if user_id:
        env_id = request.session.get("environment_id")
        if not env_id:
            # Lazy-resolve and cache the default environment in session
            env_id = store.get_default_environment_id(user_id)
            if not env_id:
                raise HTTPException(500, "No default environment found")
            request.session["environment_id"] = env_id
        return int(env_id)
    raise HTTPException(401, "Provide an X-API-Key header or log in.")


async def require_admin(user_id: int = Depends(require_auth)) -> int:
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    return user_id


def is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    user = store.get_user_by_id(user_id)
    return bool(user and user["is_admin"])


def get_session_user(request: Request) -> int | None:
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


def get_session_env(request: Request) -> int | None:
    """Return environment_id from session, or None."""
    return request.session.get("environment_id")


def _restore_username(request: Request, uid: int) -> None:
    if not request.session.get("username"):
        user = store.get_user_by_id(uid)
        if user:
            request.session["username"] = user["username"]


def _user_from_session(request: Request) -> int | None:
    uid = request.session.get("user_id")
    return int(uid) if uid else None
