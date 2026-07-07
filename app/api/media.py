"""Serve saved crops and source images — /media/crops/* and /media/sources/*."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
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
async def serve_source(filename: str, h: int | None = Query(None, ge=10, le=4000)):
    path = sources_dir() / filename
    if not path.exists():
        raise HTTPException(404, "Source image not found")
    if not h:
        return FileResponse(path)

    thumb_dir = sources_dir().parent / "thumbs"
    thumb_dir.mkdir(exist_ok=True)
    stem = Path(filename).stem
    cache_path = thumb_dir / f"{stem}_h{h}.jpg"

    if not cache_path.exists():
        from PIL import Image, ImageOps
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        if img.height > h:
            new_w = round(img.width * h / img.height)
            img = img.resize((new_w, h), Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(cache_path, "JPEG", quality=85)

    return FileResponse(cache_path, media_type="image/jpeg")
