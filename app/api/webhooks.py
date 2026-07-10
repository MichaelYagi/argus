"""Webhook management — CRUD for per-environment HTTP callbacks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl

from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()

_VALID_EVENTS = {"job.done", "detection.created", "detection.labeled", "identity.created", "identity.merged", "identity.deleted"}


def _fmt(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "events": row["events"].split(","),
        "label": row["label"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


class _CreateBody(BaseModel):
    url: HttpUrl
    events: list[str] = ["job.done"]
    label: str = ""
    secret: str | None = None


class _UpdateBody(BaseModel):
    url: HttpUrl | None = None
    events: list[str] | None = None
    label: str | None = None
    secret: str | None = None
    is_active: bool | None = None


@router.get("/api/webhooks")
async def list_webhooks(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return [_fmt(r) for r in store.list_webhooks(user_id, environment_id)]


@router.post("/api/webhooks", status_code=201)
async def create_webhook(
    body: _CreateBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    unknown = set(body.events) - _VALID_EVENTS
    if unknown:
        raise HTTPException(400, f"Unknown events: {sorted(unknown)}. Valid: {sorted(_VALID_EVENTS)}")
    events_str = ",".join(sorted(set(body.events)))
    wid = store.create_webhook(
        user_id, str(body.url), events_str, body.label, body.secret, environment_id,
    )
    row = store.get_webhook(wid, user_id)
    return _fmt(row)


@router.get("/api/webhooks/{webhook_id}")
async def get_webhook(webhook_id: int, user_id: int = Depends(require_auth)):
    row = store.get_webhook(webhook_id, user_id)
    if not row:
        raise HTTPException(404, "Webhook not found")
    return _fmt(row)


@router.put("/api/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int, body: _UpdateBody, user_id: int = Depends(require_auth),
):
    kwargs: dict = {}
    if body.url is not None:
        kwargs["url"] = str(body.url)
    if body.events is not None:
        unknown = set(body.events) - _VALID_EVENTS
        if unknown:
            raise HTTPException(400, f"Unknown events: {sorted(unknown)}")
        kwargs["events"] = ",".join(sorted(set(body.events)))
    if body.label is not None:
        kwargs["label"] = body.label
    if body.secret is not None:
        kwargs["secret"] = body.secret
    if body.is_active is not None:
        kwargs["is_active"] = int(body.is_active)
    if not store.update_webhook(webhook_id, user_id, **kwargs):
        raise HTTPException(404, "Webhook not found")
    return _fmt(store.get_webhook(webhook_id, user_id))


@router.delete("/api/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int, user_id: int = Depends(require_auth)):
    if not store.delete_webhook(webhook_id, user_id):
        raise HTTPException(404, "Webhook not found")


@router.get("/api/webhooks/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    webhook_id: int,
    limit: int = 50,
    user_id: int = Depends(require_auth),
):
    rows = store.list_deliveries(webhook_id, user_id, min(limit, 100))
    return [dict(r) for r in rows]


@router.post("/api/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, user_id: int = Depends(require_auth)):
    from app.core import webhook as _webhook
    result = _webhook.fire_test(webhook_id, user_id)
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result
