"""Minimal API key management page — replaced with full UI in step 12."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.auth import get_session_user
from app.core.security import generate_api_key, hash_api_key
from app.db import store

router = APIRouter()


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")

    rows = store.list_api_keys(user_id)
    rows_html = "".join(
        f"""<tr>
              <td>{r['label'] or '<em>unlabelled</em>'}</td>
              <td>{r['created_at']}</td>
              <td>{r['last_used_at'] or '—'}</td>
              <td>{'active' if r['is_active'] else 'revoked'}</td>
              <td>
                {'<form method="post" action="/keys/' + str(r["id"]) + '/revoke" style="display:inline">'
                 '<button type="submit">Revoke</button></form>'
                 if r['is_active'] else ''}
              </td>
            </tr>"""
        for r in rows
    )
    table = (
        f"""<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
              <thead><tr><th>Label</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table>"""
        if rows
        else "<p>No API keys yet.</p>"
    )

    new_key_html = ""
    new_key = request.session.pop("new_key", None)
    if new_key:
        new_key_html = f"""<div style="background:#f0fff0;border:1px solid #6c6;padding:12px;margin:16px 0">
            <strong>New key (copy it now — shown once):</strong><br>
            <code style="font-size:0.95em;word-break:break-all">{new_key}</code>
        </div>"""

    return HTMLResponse(f"""<!doctype html><html><head><title>Argus — API keys</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:60px auto;padding:0 16px}}
input{{padding:6px;margin-right:8px}}button{{padding:6px 12px;cursor:pointer}}
a{{margin-right:16px}}</style></head><body>
<h2>API keys</h2>
<p><a href="/dashboard">Dashboard</a></p>
{new_key_html}
{table}
<h3 style="margin-top:24px">Create new key</h3>
<form method="post" action="/keys">
  <input name="label" placeholder="Label (optional)" type="text">
  <button type="submit">Create</button>
</form>
</body></html>""")


@router.post("/keys")
async def create_key(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    label = str(form.get("label") or "").strip()

    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), label)

    # Store in session so it can be shown once on the next GET
    request.session["new_key"] = plaintext
    return RedirectResponse("/keys", status_code=303)


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: int, request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    store.revoke_api_key(key_id, user_id)
    return RedirectResponse("/keys", status_code=303)
