"""Suggested people — cluster unlabeled face detections into proposed identities.

GET /api/clusters groups residual unknown faces (matching nobody enrolled) by
similarity. Naming a cluster is done with the existing batch-label endpoint
(POST /api/detections/label), so there's no separate "name cluster" route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core import clustering, settings_cache
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


@router.get("/api/clusters")
async def get_clusters(
    threshold: float | None = Query(None, ge=0.0, le=1.0),
    min_size: int = Query(2, ge=1, le=50),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Group unlabeled face detections into suggested people. Read-only: computes
    clusters on demand, stores nothing. Name a cluster by POSTing its detection_ids
    to /api/detections/label with the same label."""
    model = store.get_active_model("face")
    if not model:
        return {"clusters": [], "unclustered": 0, "threshold": None}

    if threshold is None:
        threshold = settings_cache.cache.get_or("face.cluster_threshold", 0.5)

    rows = store.get_unknown_face_embeddings(user_id, model["id"], environment_id)
    by_id = {r["id"]: r for r in rows}
    items = [(r["id"], r["embedding"]) for r in rows]

    clusters = clustering.cluster_embeddings(items, threshold, min_size=min_size)

    clustered_ids = {did for c in clusters for did in c}
    result = []
    for c in clusters:
        rep = clustering.best_internal_score(by_id, c)
        result.append({
            "size": len(c),
            "detection_ids": c,
            "representative_crop": f"/media/crops/{by_id[rep]['crop_path']}",
            "crops": [
                {"detection_id": did, "crop_url": f"/media/crops/{by_id[did]['crop_path']}"}
                for did in c
            ],
        })

    return {
        "clusters": result,
        "unclustered": len(rows) - len(clustered_ids),
        "threshold": round(float(threshold), 4),
    }
