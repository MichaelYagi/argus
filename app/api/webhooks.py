"""Webhook management — CRUD for per-environment HTTP callbacks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl

from app.api._responses import ERR_400, ERR_401, ERR_404, ok, ok201
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()

_VALID_EVENTS = {
    "job.done",
    "detection.created", "detection.labeled", "detection.deleted",
    "identity.created", "identity.updated", "identity.merged", "identity.deleted",
    "model.activated",
}


def _fmt(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "events": [e for e in row["events"].split(",") if e],
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


_WEBHOOK_EXAMPLE = {
    "id": 1,
    "url": "https://example.com/argus-webhook",
    "events": ["detection.created", "identity.updated"],
    "label": "Shashin sync",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00Z",
}


@router.get(
    "/api/webhooks",
    responses={**ok([_WEBHOOK_EXAMPLE]), **ERR_401},
)
async def list_webhooks(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return [_fmt(r) for r in store.list_webhooks(user_id, environment_id)]


@router.post(
    "/api/webhooks",
    status_code=201,
    responses={**ok201(_WEBHOOK_EXAMPLE), **ERR_401, **ERR_400},
)
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


def _get_webhook_scoped(webhook_id: int, user_id: int, environment_id: int):
    """Fetch webhook and verify it belongs to the caller's environment."""
    row = store.get_webhook(webhook_id, user_id)
    if not row or row["environment_id"] != environment_id:
        raise HTTPException(404, "Webhook not found")
    return row


@router.get(
    "/api/webhooks/{webhook_id}",
    responses={**ok(_WEBHOOK_EXAMPLE), **ERR_401, **ERR_404},
)
async def get_webhook(
    webhook_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return _fmt(_get_webhook_scoped(webhook_id, user_id, environment_id))


@router.put(
    "/api/webhooks/{webhook_id}",
    responses={
        **ok({**_WEBHOOK_EXAMPLE, "label": "Updated label"}),
        **ERR_401,
        **ERR_404,
        **ERR_400,
    },
)
async def update_webhook(
    webhook_id: int,
    body: _UpdateBody,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    _get_webhook_scoped(webhook_id, user_id, environment_id)
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
        kwargs["secret"] = body.secret or None  # empty string clears the secret
    if body.is_active is not None:
        kwargs["is_active"] = int(body.is_active)
    if not store.update_webhook(webhook_id, user_id, **kwargs):
        raise HTTPException(404, "Webhook not found")
    return _fmt(store.get_webhook(webhook_id, user_id))


@router.delete(
    "/api/webhooks/{webhook_id}",
    status_code=204,
    responses={**ERR_401, **ERR_404},
)
async def delete_webhook(
    webhook_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    _get_webhook_scoped(webhook_id, user_id, environment_id)
    if not store.delete_webhook(webhook_id, user_id):
        raise HTTPException(404, "Webhook not found")


@router.get(
    "/api/webhooks/{webhook_id}/deliveries",
    responses={
        **ok([
            {
                "id": 1,
                "webhook_id": 1,
                "event": "detection.created",
                "status_code": 200,
                "delivered_at": "2026-01-15T10:30:05Z",
            }
        ]),
        **ERR_401,
        **ERR_404,
    },
)
async def list_webhook_deliveries(
    webhook_id: int,
    limit: int = 50,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    _get_webhook_scoped(webhook_id, user_id, environment_id)
    rows = store.list_deliveries(webhook_id, user_id, min(limit, 100))
    return [dict(r) for r in rows]


@router.post(
    "/api/webhooks/{webhook_id}/test",
    responses={
        **ok({"status_code": 200, "ok": True, "duration_ms": 142}),
        **ERR_401,
        **ERR_404,
    },
)
async def test_webhook(
    webhook_id: int,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    _get_webhook_scoped(webhook_id, user_id, environment_id)
    from app.core import webhook as _webhook
    result = _webhook.fire_test(webhook_id, user_id)
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result
