"""Webhook dispatcher — fires HTTP POST callbacks for job.done and detection.created events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 10


def fire(user_id: int, environment_id: int, event: str, payload: dict) -> None:
    """Fire all matching active webhooks for this user/env/event in daemon threads."""
    from app.db import store
    hooks = store.list_webhooks(user_id, environment_id, event)
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
            args=(hook["url"], hook["secret"], body),
            daemon=True,
        ).start()


def _deliver(url: str, secret: str | None, body: bytes) -> None:
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Argus-Signature"] = f"sha256={sig}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, content=body, headers=headers)
            if resp.status_code >= 400:
                log.warning("Webhook %s returned HTTP %s", url, resp.status_code)
    except Exception as exc:
        log.warning("Webhook delivery failed %s: %s", url, exc)
