"""Data export and import — recognition data only (identities, embeddings, detections)."""

from __future__ import annotations

import base64
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

    identities_data = []
    source_files: set[str] = set()
    crop_files:   set[str] = set()

    with store._connect() as conn:
        for iid in body.identity_ids:
            row = conn.execute(
                "SELECT * FROM identities WHERE id = ? AND user_id = ?",
                (iid, user_id),
            ).fetchone()
            if not row:
                continue

            embeddings = conn.execute(
                """SELECT fe.embedding, fe.source_image_path, m.name AS model_name
                   FROM face_embeddings fe
                   LEFT JOIN models m ON m.id = fe.model_id
                   WHERE fe.identity_id = ?""",
                (iid,),
            ).fetchall()

            detections = conn.execute(
                """SELECT d.confidence, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h,
                          d.crop_path, d.detected_at, d.review_status,
                          si.file_path AS source_image, si.width, si.height
                   FROM detections d
                   JOIN source_images si ON si.id = d.source_image_id
                   WHERE d.identity_id = ? AND d.user_id = ?""",
                (iid, user_id),
            ).fetchall()

            for e in embeddings:
                if e["source_image_path"]:
                    source_files.add(e["source_image_path"])
            for d in detections:
                source_files.add(d["source_image"])
                if d["crop_path"]:
                    crop_files.add(d["crop_path"])

            identities_data.append({
                "type": row["type"],
                "label": row["label"],
                "embeddings": [
                    {
                        "model_name": e["model_name"] or "unknown",
                        "embedding_b64": base64.b64encode(bytes(e["embedding"])).decode(),
                        "source_image": e["source_image_path"],
                    }
                    for e in embeddings
                ],
                "detections": [
                    {
                        "source_image": d["source_image"],
                        "source_width":  d["width"],
                        "source_height": d["height"],
                        "crop": d["crop_path"],
                        "confidence": d["confidence"],
                        "bbox": {"x": d["bbox_x"], "y": d["bbox_y"],
                                 "w": d["bbox_w"], "h": d["bbox_h"]},
                        "detected_at":   d["detected_at"],
                        "review_status": d["review_status"],
                    }
                    for d in detections
                ],
            })

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("argus_export.json", json.dumps({
            "version": __version__,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "identities": identities_data,
        }, indent=2))

        for filename in source_files:
            path = sources_dir() / filename
            if path.exists():
                zf.write(path, f"sources/{filename}")

        for filename in crop_files:
            path = crops_dir() / filename
            if path.exists():
                zf.write(path, f"crops/{filename}")

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

    stats = {
        "identities_created": 0,
        "identities_merged": 0,
        "embeddings_imported": 0,
        "embeddings_skipped": 0,
        "detections_imported": 0,
        "detections_skipped": 0,
        "images_copied": 0,
    }

    # Copy image files first (idempotent — skip if already present)
    sources_d = sources_dir()
    crops_d   = crops_dir()
    sources_d.mkdir(parents=True, exist_ok=True)
    crops_d.mkdir(parents=True, exist_ok=True)

    for name in zf.namelist():
        if name.startswith("sources/") and name != "sources/":
            dest = sources_d / Path(name).name
            if not dest.exists():
                dest.write_bytes(zf.read(name))
                stats["images_copied"] += 1
        elif name.startswith("crops/") and name != "crops/":
            dest = crops_d / Path(name).name
            if not dest.exists():
                dest.write_bytes(zf.read(name))
                stats["images_copied"] += 1

    zf.close()

    with store._connect() as conn:
        for id_data in export.get("identities", []):
            itype = id_data["type"]
            label = id_data["label"]

            existing = conn.execute(
                "SELECT id FROM identities WHERE user_id = ? AND type = ? AND label = ?",
                (user_id, itype, label),
            ).fetchone()

            if existing:
                identity_id = existing["id"]
                stats["identities_merged"] += 1
            else:
                conn.execute(
                    "INSERT INTO identities (user_id, type, label) VALUES (?, ?, ?)",
                    (user_id, itype, label),
                )
                identity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                stats["identities_created"] += 1

            # Embeddings — skip if same model + source image already enrolled
            for emb in id_data.get("embeddings", []):
                model_name   = emb.get("model_name", "unknown")
                source_image = emb.get("source_image")

                dup = conn.execute(
                    """SELECT fe.id FROM face_embeddings fe
                       LEFT JOIN models m ON m.id = fe.model_id
                       WHERE fe.identity_id = ? AND fe.source_image_path = ?
                         AND (m.name = ? OR (fe.model_id IS NULL AND ? = 'unknown'))""",
                    (identity_id, source_image, model_name, model_name),
                ).fetchone()

                if dup:
                    stats["embeddings_skipped"] += 1
                    continue

                model_row = conn.execute(
                    "SELECT id FROM models WHERE name = ?", (model_name,)
                ).fetchone()
                model_id = model_row["id"] if model_row else None

                conn.execute(
                    """INSERT INTO face_embeddings
                       (identity_id, model_id, embedding, source_image_path)
                       VALUES (?, ?, ?, ?)""",
                    (identity_id, model_id,
                     base64.b64decode(emb["embedding_b64"]), source_image),
                )
                stats["embeddings_imported"] += 1

            # Detections — skip if same source + bbox already linked to this identity
            for det in id_data.get("detections", []):
                source_image = det["source_image"]
                bbox = det.get("bbox", {})
                bx, by, bw, bh = bbox.get("x",0), bbox.get("y",0), bbox.get("w",0), bbox.get("h",0)

                src_row = conn.execute(
                    "SELECT id FROM source_images WHERE user_id = ? AND file_path = ?",
                    (user_id, source_image),
                ).fetchone()

                if src_row:
                    source_image_id = src_row["id"]
                else:
                    w = det.get("source_width", 0)
                    h = det.get("source_height", 0)
                    conn.execute(
                        "INSERT INTO source_images (user_id, file_path, width, height) VALUES (?,?,?,?)",
                        (user_id, source_image, w, h),
                    )
                    source_image_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                dup = conn.execute(
                    """SELECT id FROM detections
                       WHERE identity_id = ? AND user_id = ? AND source_image_id = ?
                         AND bbox_x = ? AND bbox_y = ? AND bbox_w = ? AND bbox_h = ?""",
                    (identity_id, user_id, source_image_id, bx, by, bw, bh),
                ).fetchone()

                if dup:
                    stats["detections_skipped"] += 1
                    continue

                conn.execute(
                    """INSERT INTO detections
                       (user_id, identity_id, source_image_id, type, confidence,
                        bbox_x, bbox_y, bbox_w, bbox_h, crop_path,
                        review_status, detected_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (user_id, identity_id, source_image_id, itype,
                     det.get("confidence", 0.0), bx, by, bw, bh,
                     det.get("crop", ""),
                     det.get("review_status", "confirmed"),
                     det.get("detected_at")),
                )
                stats["detections_imported"] += 1

    return stats
