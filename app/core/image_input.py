"""Image acquisition and validation — shared by detect and enroll routes.

All three input paths (file, URL, base64) converge to raw bytes, then to a
validated PIL Image.  Nothing downstream branches on input source.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from fastapi import HTTPException, Request

from app.core import settings_cache

# Register HEIC/HEIF support with Pillow if available
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF", "HEIF", "MPO"})


async def acquire_image(request: Request) -> bytes:
    """Return raw image bytes from whichever of file/URL/base64 was supplied.

    Raises 400 if zero or more than one input is provided.
    """
    content_type = request.headers.get("content-type", "")

    file_bytes: bytes | None = None
    image_url: str | None = None
    image_base64: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get("file")
        if file_field is not None and hasattr(file_field, "read"):
            file_bytes = await file_field.read() or None
        raw_url = form.get("image_url")
        image_url = str(raw_url) if raw_url else None
        raw_b64 = form.get("image_base64")
        image_base64 = str(raw_b64) if raw_b64 else None
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        image_url = body.get("image_url")
        image_base64 = body.get("image_base64")
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    provided = sum(x is not None for x in [file_bytes, image_url, image_base64])
    if provided != 1:
        raise HTTPException(400, "Provide exactly one of: file, image_url, image_base64")

    if file_bytes is not None:
        return file_bytes
    if image_url is not None:
        return await fetch_url(image_url)
    assert image_base64 is not None  # exactly-one guard above ensures this
    return decode_base64(image_base64)


async def acquire_image_slot(request: Request, slot: int) -> bytes:
    """Return raw bytes for a numbered image slot (verify takes two images).

    Mirrors acquire_image's one-of-three rule, but with slot-prefixed field names:
    ``file{n}`` (multipart) / ``image{n}_url`` / ``image{n}_base64``.
    """
    content_type = request.headers.get("content-type", "")
    f, u, b = f"file{slot}", f"image{slot}_url", f"image{slot}_base64"

    file_bytes: bytes | None = None
    image_url: str | None = None
    image_base64: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get(f)
        if file_field is not None and hasattr(file_field, "read"):
            file_bytes = await file_field.read() or None
        raw_url = form.get(u)
        image_url = str(raw_url) if raw_url else None
        raw_b64 = form.get(b)
        image_base64 = str(raw_b64) if raw_b64 else None
    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        image_url = body.get(u)
        image_base64 = body.get(b)
    else:
        raise HTTPException(400, "Content-Type must be multipart/form-data or application/json")

    provided = sum(x is not None for x in [file_bytes, image_url, image_base64])
    if provided != 1:
        raise HTTPException(400, f"Provide exactly one of: {f}, {u}, {b}")

    if file_bytes is not None:
        return file_bytes
    if image_url is not None:
        return await fetch_url(image_url)
    assert image_base64 is not None  # exactly-one guard above ensures this
    return decode_base64(image_base64)


async def read_body_field(request: Request, key: str, default: str | None = None) -> str | None:
    """Read one string field from a multipart form or JSON body.

    Returns ``default`` when the key is absent or on any parse error.
    Starlette caches form/JSON after first access, so calling this
    alongside ``acquire_image`` or multiple times per request is free.
    """
    ct = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in ct:
            v = (await request.form()).get(key)
            return str(v) if v is not None else default
        if "application/json" in ct:
            v = (await request.json()).get(key)
            return str(v) if v is not None else default
    except Exception:
        pass
    return default


def open_and_validate(image_bytes: bytes) -> Any:
    """Open image bytes with Pillow, validate format by content sniffing.

    Returns a PIL Image.  Raises 400 for corrupt data, 415 for unsupported format.
    """
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception:
        raise HTTPException(400, "Could not decode image data")

    fmt = img.format
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(415, f"Unsupported image format: {fmt or 'unknown'}")

    if fmt in ("GIF", "MPO"):
        img.seek(0)

    # Apply EXIF orientation so that crops and source images are upright.
    # Phone cameras store pixels sideways and embed a rotation tag; without this,
    # face crops are saved rotated and InsightFace detects faces at the wrong angle.
    img = ImageOps.exif_transpose(img)

    return img


def to_rgb_array(img: Any) -> Any:
    """Convert a PIL Image to an HxWx3 uint8 numpy array (RGB)."""
    import numpy as np

    return np.array(img.convert("RGB"))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def fetch_url(url: str) -> bytes:
    import httpx

    timeout = settings_cache.cache.get_or("system.url_fetch_timeout_seconds", 10)
    max_bytes = settings_cache.cache.get_or("system.url_fetch_max_size_mb", 25) * 1024 * 1024

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(400, f"URL fetch returned HTTP {resp.status_code}")
                ct = resp.headers.get("content-type", "")
                if ct and not ct.startswith("image/") and "octet-stream" not in ct:
                    raise HTTPException(415, f"URL returned non-image content-type: {ct}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        mb = settings_cache.cache.get_or("system.url_fetch_max_size_mb", 25)
                        raise HTTPException(413, f"Image exceeds maximum size ({mb} MB)")
                    chunks.append(chunk)
                return b"".join(chunks)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Failed to fetch image URL: {exc}") from exc


def decode_base64(data: str) -> bytes:
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(400, "Invalid base64 data")
