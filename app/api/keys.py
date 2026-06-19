"""API key management — POST /api/keys, GET /api/keys, DELETE /api/keys/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_auth
from app.core.security import generate_api_key, hash_api_key
from app.db import store

router = APIRouter()


class _CreateKey(BaseModel):
    label: str = ""


@router.get("/api/keys")
async def list_keys(user_id: int = Depends(require_auth)):
    rows = store.list_api_keys(user_id)
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "is_active": bool(r["is_active"]),
        }
        for r in rows
    ]


@router.post("/api/keys", status_code=201)
async def create_key(body: _CreateKey, user_id: int = Depends(require_auth)):
    plaintext = generate_api_key()
    key_id = store.create_api_key(user_id, hash_api_key(plaintext), body.label)
    return {
        "id": key_id,
        "label": body.label,
        "key": plaintext,  # shown once — not stored
    }


@router.delete("/api/keys/{key_id}", status_code=204)
async def revoke_key(key_id: int, user_id: int = Depends(require_auth)):
    if not store.revoke_api_key(key_id, user_id):
        raise HTTPException(404, "Key not found")
