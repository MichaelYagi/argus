"""API key management — POST /api/keys, GET /api/keys, DELETE /api/keys/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import require_auth
from app.core.security import generate_api_key, hash_api_key, key_hint
from app.db import store

router = APIRouter()


class _CreateKey(BaseModel):
    label: str = ""
    environment_id: int | None = None


@router.get("/api/keys")
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


@router.post("/api/keys", status_code=201)
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


@router.put("/api/keys/{key_id}", status_code=200)
async def rename_key(key_id: int, body: _RenameKey, user_id: int = Depends(require_auth)):
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "Label is required")
    if not store.rename_api_key(key_id, user_id, label):
        raise HTTPException(404, "Key not found")
    return {"id": key_id, "label": label}


@router.delete("/api/keys/{key_id}", status_code=204)
async def revoke_key(key_id: int, user_id: int = Depends(require_auth)):
    if not store.revoke_api_key(key_id, user_id):
        raise HTTPException(404, "Key not found")
