"""Detect page — single or bulk image detection."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.core.auth import get_session_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/detect")
@router.get("/test")   # legacy redirect
@router.get("/bulk")   # legacy redirect
async def detect_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "bulk.html", {
        "username": request.session.get("username", ""),
        "active": "detect",
        "version": __version__,
    })
