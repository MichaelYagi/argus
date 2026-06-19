"""Serve saved crops and source images — /media/crops/* and /media/sources/*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.paths import crops_dir, sources_dir

router = APIRouter()


@router.get("/media/crops/{filename}")
async def serve_crop(filename: str):
    path = crops_dir() / filename
    if not path.exists():
        raise HTTPException(404, "Crop not found")
    return FileResponse(path)


@router.get("/media/sources/{filename}")
async def serve_source(filename: str):
    path = sources_dir() / filename
    if not path.exists():
        raise HTTPException(404, "Source image not found")
    return FileResponse(path)
