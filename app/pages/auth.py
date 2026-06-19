"""Sign up, sign in, and sign out page routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.security import (
    REMEMBER_MAX_AGE,
    create_remember_token,
    hash_password,
    verify_password,
)
from app.db import store

router = APIRouter()

_COOKIE = "argus_remember"


# ---------------------------------------------------------------------------
# Sign up
# ---------------------------------------------------------------------------

@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str = ""):
    return _signup_html(error)


@router.post("/signup")
async def signup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
):
    username = username.strip()
    if not username:
        return _signup_html("Username is required.")
    if len(password) < 8:
        return _signup_html("Password must be at least 8 characters.")
    if password != confirm:
        return _signup_html("Passwords do not match.")
    if store.get_user_by_username(username):
        return _signup_html("Username already taken.")

    is_first = store.count_users() == 0
    pw_hash = hash_password(password)
    user_id = store.create_user(username, pw_hash, is_admin=is_first)

    request.session["user_id"] = user_id
    request.session["username"] = username
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return _login_html(error)


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember: str = Form(default=""),
):
    row = store.get_user_by_username(username.strip())
    if not row or not verify_password(password, row["password_hash"]):
        return _login_html("Invalid username or password.")

    request.session["user_id"] = row["id"]
    request.session["username"] = row["username"]

    response = RedirectResponse("/", status_code=303)

    if remember:
        secret = os.environ.get("SECRET_KEY", "change-me")
        token = create_remember_token(row["id"], secret)
        response.set_cookie(
            _COOKIE,
            token,
            max_age=REMEMBER_MAX_AGE,
            httponly=True,
            samesite="lax",
        )

    return response


# ---------------------------------------------------------------------------
# Sign out
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Minimal HTML (replaced with Jinja templates in step 12)
# ---------------------------------------------------------------------------

def _signup_html(error: str = "") -> HTMLResponse:
    err = f'<p class="err">{error}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html><head><title>Argus — Sign up</title>
<style>body{{font-family:sans-serif;max-width:360px;margin:80px auto;padding:0 16px}}
input{{display:block;width:100%;margin:8px 0;padding:8px;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#1a1a1a;color:#fff;border:none;cursor:pointer}}
.err{{color:#c00}}</style></head><body>
<h2>Create account</h2>{err}
<form method="post">
<input name="username" placeholder="Username" required autofocus>
<input name="password" type="password" placeholder="Password (min 8 chars)" required>
<input name="confirm" type="password" placeholder="Confirm password" required>
<button type="submit">Sign up</button>
</form>
<p style="margin-top:16px"><a href="/login">Already have an account?</a></p>
</body></html>""")


def _login_html(error: str = "") -> HTMLResponse:
    err = f'<p class="err">{error}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html><head><title>Argus — Sign in</title>
<style>body{{font-family:sans-serif;max-width:360px;margin:80px auto;padding:0 16px}}
input[type=text],input[type=password]{{display:block;width:100%;margin:8px 0;padding:8px;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#1a1a1a;color:#fff;border:none;cursor:pointer}}
.err{{color:#c00}}.row{{display:flex;align-items:center;gap:8px;margin:8px 0}}</style></head><body>
<h2>Sign in</h2>{err}
<form method="post">
<input type="text" name="username" placeholder="Username" required autofocus>
<input type="password" name="password" placeholder="Password" required>
<div class="row"><input type="checkbox" name="remember" id="rem" value="1">
<label for="rem">Remember me</label></div>
<button type="submit">Sign in</button>
</form>
<p style="margin-top:16px"><a href="/signup">Create an account</a></p>
</body></html>""")
