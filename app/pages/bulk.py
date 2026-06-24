"""Detect page — single or bulk image detection."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.pages.main_pages import _base

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/detect")
@router.get("/test")   # legacy redirect
@router.get("/bulk")   # legacy redirect
async def detect_page(request: Request):
    ctx = _base(request, "detect")
    if not ctx:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "bulk.html", ctx)
