"""Data export and import — recognition data only (identities, embeddings, detections)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import __version__
from app.core.auth import require_auth
from app.core.paths import crops_dir, sources_dir
from app.db import store

router = APIRouter()


class _ExportBody(BaseModel):
    identity_ids: list[int]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.post("/api/export")
async def export_data(body: _ExportBody, user_id: int = Depends(require_auth)):
    if not body.identity_ids:
        raise HTTPException(400, "Select at least one identity to export")

    identities_data = store.export_identity_data(user_id, body.identity_ids)

    # Collect all image filenames referenced by the exported data.
    source_files: set[str] = set()
    crop_files:   set[str] = set()
    for id_data in identities_data:
        for e in id_data["embeddings"]:
            if e["source_image"]:
                source_files.add(e["source_image"])
        for d in id_data["detections"]:
            source_files.add(d["source_image"])
            if d["crop"]:
                crop_files.add(d["crop"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # JSON compresses well
        zf.writestr(
            zipfile.ZipInfo("argus_export.json"),
            json.dumps({
                "version": __version__,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "identities": identities_data,
            }, indent=2).encode(),
            zipfile.ZIP_DEFLATED,
        )

        # Images are already compressed — ZIP_STORED skips pointless re-compression
        for filename in source_files:
            path = sources_dir() / filename
            if path.exists():
                zf.write(path, f"sources/{filename}", zipfile.ZIP_STORED)

        for filename in crop_files:
            path = crops_dir() / filename
            if path.exists():
                zf.write(path, f"crops/{filename}", zipfile.ZIP_STORED)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=argus_export.zip"},
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/api/import")
async def import_data(
    file: UploadFile = File(...),
    user_id: int = Depends(require_auth),
):
    content = await file.read()

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid zip file")

    if "argus_export.json" not in zf.namelist():
        raise HTTPException(400, "Not an Argus export — missing argus_export.json")

    try:
        export = json.loads(zf.read("argus_export.json"))
    except json.JSONDecodeError:
        raise HTTPException(400, "Corrupt export — invalid JSON")

    # Copy image files first (idempotent — skip if already present)
    sources_d = sources_dir()
    crops_d   = crops_dir()
    sources_d.mkdir(parents=True, exist_ok=True)
    crops_d.mkdir(parents=True, exist_ok=True)

    images_copied = 0
    for name in zf.namelist():
        if name.startswith("sources/") and name != "sources/":
            dest = sources_d / Path(name).name
            if not dest.exists():
                dest.write_bytes(zf.read(name))
                images_copied += 1
        elif name.startswith("crops/") and name != "crops/":
            dest = crops_d / Path(name).name
            if not dest.exists():
                dest.write_bytes(zf.read(name))
                images_copied += 1

    zf.close()

    stats = store.import_identity_data(user_id, export.get("identities", []))
    stats["images_copied"] = images_copied
    return stats
