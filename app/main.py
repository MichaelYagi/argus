"""FastAPI application — single app object used by both Docker and `python -m app`."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api import detect, health, keys, media
from app.core import settings_cache
from app.db import store
from app.pages import auth

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    if os.environ.get("SECRET_KEY", "change-me") == "change-me":
        log.warning("SECRET_KEY is not set — sessions are insecure. Set SECRET_KEY in .env.")
    store.init_db()
    settings_cache.cache.load()
    yield


app = FastAPI(title="Argus", version=__version__, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "change-me"),
    session_cookie="argus_session",
    https_only=False,  # LAN tool — allow plain HTTP
    same_site="lax",
)

app.include_router(health.router)
app.include_router(detect.router)
app.include_router(media.router)
app.include_router(keys.router)
app.include_router(auth.router)


@app.get("/")
async def root(request: Request):
    from app.core.auth import get_session_user
    if get_session_user(request):
        return RedirectResponse("/dashboard")
    if store.count_users() == 0:
        return RedirectResponse("/signup")
    return RedirectResponse("/login")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    from app.core.auth import get_session_user
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")
    username = request.session.get("username", "")
    return HTMLResponse(f"""<!doctype html><html><head><title>Argus</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 16px}}
a{{margin-right:16px}}</style></head><body>
<h2>Argus</h2>
<p>Signed in as <strong>{username}</strong></p>
<p><a href="/api/docs">API docs</a><a href="/api/keys">Manage API keys</a></p>
<form method="post" action="/logout" style="margin-top:24px">
<button type="submit">Sign out</button></form>
</body></html>""")
