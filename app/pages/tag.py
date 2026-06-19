"""Tag page — image with clickable face bbox overlays."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.auth import get_session_user
from app.db import store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/tag/{source_image_id}", response_class=HTMLResponse)
async def tag_page(source_image_id: int, request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")

    src = store.get_source_image(source_image_id, user_id)
    if not src:
        return HTMLResponse("<h2>Image not found</h2>", status_code=404)

    faces = store.get_image_detections(source_image_id, user_id, det_type="face")
    face_data = [
        {
            "id": r["id"],
            "x": r["bbox_x"], "y": r["bbox_y"],
            "w": r["bbox_w"], "h": r["bbox_h"],
            "label": r["identity_label"] or "",
            "confidence": round(r["confidence"], 3),
        }
        for r in faces
    ]

    return templates.TemplateResponse(request, "tag.html", {
        "username": request.session.get("username", ""),
        "image_url": f"/media/sources/{src['file_path']}",
        "nat_w": src["width"],
        "nat_h": src["height"],
        "faces_json": json.dumps(face_data),
    })
