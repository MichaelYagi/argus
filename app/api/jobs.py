"""Job status endpoints — GET /api/jobs, GET /api/jobs/{job_id}, DELETE /api/jobs/{job_id}."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import require_auth, require_env_id
from app.db import store

router = APIRouter()


def _fmt(row) -> dict:
    result_raw = row["result"]
    return {
        "job_id":     row["id"],
        "type":       row["type"],
        "status":     row["status"],
        "result":     json.loads(result_raw) if result_raw else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/api/jobs")
async def list_jobs(
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    return [_fmt(r) for r in store.list_jobs(user_id, environment_id=environment_id)]


@router.get("/api/jobs/{job_id}")
async def get_job(
    job_id: str,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    row = store.get_job(job_id, user_id, environment_id)
    if not row:
        raise HTTPException(404, "Job not found")
    return _fmt(row)


@router.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    user_id: int = Depends(require_auth),
    environment_id: int = Depends(require_env_id),
):
    if not store.delete_job(job_id, user_id, environment_id):
        raise HTTPException(404, "Job not found")
