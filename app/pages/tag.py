"""Tag page — image with clickable face bbox overlays."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.core import settings_cache
from app.core.auth import get_session_env, get_session_user, is_admin
from app.db import store
from app.pages.main_pages import engine_flags as _engine_flags

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/tag/{source_image_id}", response_class=HTMLResponse)
async def tag_page(source_image_id: int, request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse("/login")

    env_id = get_session_env(request)
    src = store.get_source_image(source_image_id, user_id, env_id)
    if not src:
        return HTMLResponse("<h2>Image not found</h2>", status_code=404)

    faces = store.get_image_detections(source_image_id, user_id, det_type="face", environment_id=env_id)
    best_match = settings_cache.cache.get_or("face.match_strategy", "best") != "average"
    _cache: dict = {}  # identity_id -> representative bytes, or list of reference blobs

    def _similarity(row):
        iid = row["identity_id"]
        if iid is None:
            return None
        if iid not in _cache:
            _cache[iid] = (store.get_identity_reference_blobs(iid, user_id, env_id) if best_match
                           else store.get_representative_embedding(iid, user_id, env_id))
        ref = _cache[iid]
        return (store.best_cosine(row["embedding"], ref) if best_match
                else store.cosine_similarity(row["embedding"], ref))

    def _attrs(row):
        try:
            raw = row["attributes"]
        except (IndexError, KeyError):
            raw = None
        data = {}
        if raw:
            try:
                data = json.loads(raw) or {}
            except (ValueError, TypeError):
                data = {}
        return data.get("age"), data.get("gender"), data.get("pose")

    face_data = []
    for r in faces:
        age, gender, pose = _attrs(r)
        face_data.append({
            "id": r["id"],
            "x": r["bbox_x"], "y": r["bbox_y"],
            "w": r["bbox_w"], "h": r["bbox_h"],
            "label": r["identity_label"] or "",
            "confidence": r["confidence"],
            "review_status": r["review_status"],
            "similarity": _similarity(r),
            "age": age, "gender": gender, "pose": pose,
        })

    objects = store.get_image_detections(source_image_id, user_id, det_type="object", environment_id=env_id)
    object_data = [{
        "id": r["id"],
        "x": r["bbox_x"], "y": r["bbox_y"],
        "w": r["bbox_w"], "h": r["bbox_h"],
        "label": r["identity_label"] or "",
        "confidence": r["confidence"],
    } for r in objects]

    environments = store.list_environments(user_id)
    env_name = next((e["name"] for e in environments if e["id"] == env_id), "default")
    try:
        image_tags = json.loads(src["image_tags"]) if src["image_tags"] else []
    except (ValueError, TypeError):
        image_tags = []

    return templates.TemplateResponse(request, "tag.html", {
        "username": request.session.get("username", ""),
        "is_admin": is_admin(user_id),
        "version": __version__,
        "source_image_id": source_image_id,
        "external_ref": src["external_ref"] or "",
        "image_url": f"/media/sources/{src['file_path']}",
        "nat_w": src["width"],
        "nat_h": src["height"],
        "image_tags": image_tags,
        "faces_json": json.dumps(face_data),
        "objects_json": json.dumps(object_data),
        "environments": [dict(e) for e in environments],
        "environment_id": env_id,
        "environment_name": env_name,
        **_engine_flags(),
    })
