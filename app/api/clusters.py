"""Suggested people — cluster unlabeled face detections into proposed identities.

GET /api/clusters groups residual unknown faces (matching nobody enrolled) by
similarity. Naming a cluster is done with the existing batch-label endpoint
(POST /api/detections/label), so there's no separate "name cluster" route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api._responses import ERR_401, ok
from app.core import clustering, settings_cache
from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


def _compute(user_id: int, environment_id: int, threshold: float | None, min_size: int):
    """Cluster the user's residual unknown faces. Returns (by_id, rows, clusters,
    threshold), or None when no face model is active."""
    model = store.get_active_model("face")
    if not model:
        return None
    if threshold is None:
        threshold = settings_cache.cache.get_or("face.cluster_threshold", 0.5)
    rows = store.get_unknown_face_embeddings(user_id, model["id"], environment_id)
    by_id = {r["id"]: r for r in rows}
    items = [(r["id"], r["embedding"]) for r in rows]
    clusters = clustering.cluster_embeddings(items, threshold, min_size=min_size)
    return by_id, rows, clusters, threshold


@router.get(
    "/api/clusters",
    responses={
        **ok({
            "clusters": [
                {
                    "size": 3,
                    "detection_ids": [12, 45, 78],
                    "representative_crop": "/media/crops/abc123.jpg",
                    "crops": [
                        {
                            "detection_id": 12,
                            "crop_url": "/media/crops/abc123.jpg",
                            "source_image_url": "/media/sources/def456.jpg",
                            "bbox": {"x": 120, "y": 80, "w": 60, "h": 75},
                        }
                    ],
                }
            ],
            "unclustered": 3,
            "threshold": 0.5,
        }),
        **ERR_401,
    },
)
async def get_clusters(
    threshold: float | None = Query(None, ge=0.0, le=1.0),
    min_size: int = Query(2, ge=1, le=50),
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Group unlabeled face detections into suggested people. Read-only: computes
    clusters on demand, stores nothing. Name a cluster by POSTing its detection_ids
    to /api/detections/label with the same label."""
    computed = _compute(user_id, environment_id, threshold, min_size)
    if computed is None:
        return {"clusters": [], "unclustered": 0, "threshold": None}
    by_id, rows, clusters, threshold = computed

    clustered_ids = {did for c in clusters for did in c}
    result = []
    for c in clusters:
        rep = clustering.best_internal_score(by_id, c)
        result.append({
            "size": len(c),
            "detection_ids": c,
            "representative_crop": f"/media/crops/{by_id[rep]['crop_path']}",
            "crops": [
                {
                    "detection_id": did,
                    "crop_url": f"/media/crops/{by_id[did]['crop_path']}",
                    "source_image_url": (
                        f"/media/sources/{by_id[did]['source_image_path']}"
                        if by_id[did]["source_image_path"] else None
                    ),
                    "bbox": {
                        "x": by_id[did]["bbox_x"], "y": by_id[did]["bbox_y"],
                        "w": by_id[did]["bbox_w"], "h": by_id[did]["bbox_h"],
                    },
                }
                for did in c
            ],
        })

    return {
        "clusters": result,
        "unclustered": len(rows) - len(clustered_ids),
        "threshold": round(float(threshold), 4),
    }


@router.get(
    "/api/clusters/count",
    responses={**ok({"count": 4}), **ERR_401},
)
async def clusters_count(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    """Number of suggested-people groups, for the nav notification dot. Uses the
    default grouping threshold."""
    computed = _compute(user_id, environment_id, None, 2)
    return {"count": 0 if computed is None else len(computed[2])}
