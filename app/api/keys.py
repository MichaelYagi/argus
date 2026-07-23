"""API key management — POST /api/keys, GET /api/keys, DELETE /api/keys/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api._responses import ERR_400, ERR_401, ERR_404, ok, ok201
from app.core.auth import require_auth
from app.core.security import generate_api_key, hash_api_key, key_hint
from app.db import store

router = APIRouter()


class _CreateKey(BaseModel):
    label: str = ""
    environment_id: int | None = None


@router.get(
    "/api/keys",
    responses={
        **ok([
            {
                "id": 1,
                "label": "Shashin integration",
                "key_hint": "argus_...a1b2",
                "environment_id": 1,
                "environment_name": "Home",
                "created_at": "2026-01-01T00:00:00Z",
                "last_used_at": "2026-01-15T10:30:00Z",
                "is_active": True,
            }
        ]),
        **ERR_401,
    },
)
async def list_keys(user_id: int = Depends(require_auth)):
    rows = store.list_api_keys(user_id)
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "key_hint": r["key_hint"],
            "environment_id": r["environment_id"],
            "environment_name": r["environment_name"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "is_active": bool(r["is_active"]),
        }
        for r in rows
    ]


@router.post(
    "/api/keys",
    status_code=201,
    responses={
        **ok201({
            "id": 2,
            "label": "New integration",
            "environment_id": 1,
            "environment_name": "Home",
            "key": "argus_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        }),
        **ERR_401,
    },
)
async def create_key(
    request: Request,
    body: _CreateKey,
    user_id: int = Depends(require_auth),
):
    env_id = body.environment_id or request.session.get("environment_id") or None
    plaintext = generate_api_key()
    key_id = store.create_api_key(user_id, hash_api_key(plaintext), body.label, env_id, key_hint(plaintext))
    env = store.get_environment(env_id, user_id) if env_id else None
    return {
        "id": key_id,
        "label": body.label,
        "environment_id": env_id,
        "environment_name": env["name"] if env else None,
        "key": plaintext,  # shown once — not stored
    }


class _RenameKey(BaseModel):
    label: str


@router.put(
    "/api/keys/{key_id}",
    status_code=200,
    responses={
        **ok({"id": 1, "label": "Updated label"}),
        **ERR_401,
        **ERR_404,
        **ERR_400,
    },
)
async def rename_key(key_id: int, body: _RenameKey, user_id: int = Depends(require_auth)):
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "Label is required")
    if not store.rename_api_key(key_id, user_id, label):
        raise HTTPException(404, "Key not found")
    return {"id": key_id, "label": label}


@router.delete(
    "/api/keys/{key_id}",
    status_code=204,
    responses={**ERR_401, **ERR_404},
)
async def revoke_key(key_id: int, user_id: int = Depends(require_auth)):
    if not store.revoke_api_key(key_id, user_id):
        raise HTTPException(404, "Key not found")
