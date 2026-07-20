"""Serve saved crops and source images — /media/crops/* and /media/sources/*."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.auth import require_auth
from app.core.paths import crops_dir, sources_dir

router = APIRouter()


def _thumbnail(path: Path, h: int) -> Path:
    """Return path to a cached JPEG thumbnail at max height h, generating if needed.
    Cache lives in a 'thumbs' subdirectory alongside the originals."""
    cache_dir = path.parent / "thumbs"
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / f"{path.stem}_h{h}.jpg"
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
    return cache_path


@router.get("/media/crops/{filename}")
async def serve_crop(
    filename: str,
    h: int | None = Query(None, ge=10, le=4000),
    _user_id: int = Depends(require_auth),
):
    path = crops_dir() / filename
    if not path.exists():
        raise HTTPException(404, "Crop not found")
    if not h:
        return FileResponse(path)
    return FileResponse(_thumbnail(path, h), media_type="image/jpeg")


@router.get("/media/sources/{filename}")
async def serve_source(
    filename: str,
    h: int | None = Query(None, ge=10, le=4000),
    _user_id: int = Depends(require_auth),
):
    path = sources_dir() / filename
    if not path.exists():
        # Fall back to any cached thumbnail if the original was deleted externally.
        stem = Path(filename).stem
        thumbs = list((sources_dir() / "thumbs").glob(f"{stem}_h*.jpg"))
        if thumbs:
            # Pick the largest available thumbnail.
            thumbs.sort(key=lambda p: int(p.stem.rsplit("_h", 1)[-1]), reverse=True)
            best = thumbs[0]
            if h:
                return FileResponse(best, media_type="image/jpeg")
            return FileResponse(best, media_type="image/jpeg")
        raise HTTPException(404, "Source image not found")
    if not h:
        return FileResponse(path)
    return FileResponse(_thumbnail(path, h), media_type="image/jpeg")
