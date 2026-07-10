"""Webhook dispatcher — fires HTTP POST callbacks for job.done and detection.created events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone

from app.db import store

log = logging.getLogger(__name__)

_TIMEOUT = 10


def fire_broadcast(event: str, payload: dict) -> None:
    """Fire all active webhooks subscribed to event, regardless of user or environment.
    Used for system-level events (e.g. model.activated) that are not scoped to one env."""
    hooks = store.list_webhooks_for_event(event)
    if not hooks:
        return
    body = json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }).encode()
    for hook in hooks:
        threading.Thread(
            target=_deliver,
            args=(hook["id"], hook["url"], hook["secret"], body, event),
            daemon=True,
        ).start()


def fire(user_id: int, environment_id: int, event: str, payload: dict) -> None:
    """Fire all matching active webhooks for this user/env/event in daemon threads."""
    hooks = store.list_webhooks(user_id, environment_id, event, active_only=True)
    if not hooks:
        return
    body = json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }).encode()
    for hook in hooks:
        threading.Thread(
            target=_deliver,
            args=(hook["id"], hook["url"], hook["secret"], body, event),
            daemon=True,
        ).start()


def fire_detection_labeled(
    detection_id: int, user_id: int, environment_id: int,
    identity_id: int | None = None, label: str | None = None,
) -> None:
    """Fire detection.labeled — fetches missing payload fields from the store."""
    det = store.get_detection(detection_id, user_id, environment_id)
    if not det:
        return
    iid = identity_id or det["identity_id"]
    if not iid:
        return
    if label is None:
        ident = store.get_identity(iid, user_id, environment_id)
        label = ident["label"] if ident else str(iid)
    fire(user_id, environment_id, "detection.labeled", {
        "detection_id": detection_id,
        "source_image_id": det["source_image_id"],
        "identity_id": iid,
        "label": label,
        "type": det["type"],
    })


def fire_test(webhook_id: int, user_id: int) -> dict | None:
    """Send a synthetic ping to the webhook synchronously; return outcome or None if not found."""
    hook = store.get_webhook(webhook_id, user_id)
    if not hook:
        return None
    body = json.dumps({
        "event": "ping",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"message": "Test ping from Argus"},
    }).encode()
    status_code, duration_ms, error = _send(hook["url"], hook["secret"], body)
    try:
        store.log_delivery(webhook_id, "ping", status_code, duration_ms, error, is_test=1)
    except Exception:
        pass
    ok = error is None and status_code is not None and status_code < 400
    return {"status_code": status_code, "duration_ms": duration_ms, "ok": ok, "error": error}


def _deliver(webhook_id: int, url: str, secret: str | None, body: bytes, event: str) -> None:
    status_code, duration_ms, error = _send(url, secret, body)
    try:
        store.log_delivery(webhook_id, event, status_code, duration_ms, error)
    except Exception:
        pass


def _send(url: str, secret: str | None, body: bytes) -> tuple[int | None, int, str | None]:
    """POST body to url; return (status_code, duration_ms, error_string_or_None)."""
    import httpx
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Argus-Signature"] = f"sha256={sig}"
    t0 = time.monotonic()
    status_code: int | None = None
    error: str | None = None
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, content=body, headers=headers)
            status_code = resp.status_code
            if resp.status_code >= 400:
                log.warning("Webhook %s returned HTTP %s", url, resp.status_code)
    except Exception as exc:
        error = str(exc)
        log.warning("Webhook delivery failed %s: %s", url, exc)
    duration_ms = int((time.monotonic() - t0) * 1000)
    return status_code, duration_ms, error
