"""Environment management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api._responses import ERR_400, ERR_401, ERR_404, ERR_409, ok, ok201
from app.api._utils import delete_crops, delete_sources, gc_source_files
from app.core.auth import require_auth
from app.db import store

router = APIRouter()


class EnvCreate(BaseModel):
    name: str


class EnvRename(BaseModel):
    name: str


@router.get(
    "/api/environments",
    responses={
        **ok([
            {
                "id": 1,
                "name": "Home",
                "created_at": "2026-01-01T00:00:00Z",
                "identities": 12,
                "detections": 348,
            }
        ]),
        **ERR_401,
    },
)
async def list_environments(user_id: int = Depends(require_auth)):
    envs = store.list_environments(user_id)
    result = []
    for e in envs:
        stats = store.get_environment_stats(e["id"], user_id)
        result.append({"id": e["id"], "name": e["name"], "created_at": e["created_at"], **stats})
    return result


@router.post(
    "/api/environments",
    status_code=201,
    responses={
        **ok201({"id": 2, "name": "Office", "created_at": "2026-01-15T10:00:00Z", "identities": 0, "detections": 0}),
        **ERR_401,
        **ERR_400,
        **ERR_409,
    },
)
async def create_environment(body: EnvCreate, user_id: int = Depends(require_auth)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    try:
        env_id = store.create_environment(user_id, name)
    except store.DuplicateError:
        raise HTTPException(409, "An environment with that name already exists")
    env = store.get_environment(env_id, user_id)
    return {"id": env["id"], "name": env["name"], "created_at": env["created_at"],
            "identities": 0, "detections": 0}


@router.get(
    "/api/environments/{env_id}",
    responses={
        **ok({"id": 1, "name": "Home", "created_at": "2026-01-01T00:00:00Z", "identities": 12, "detections": 348}),
        **ERR_401,
        **ERR_404,
    },
)
async def get_environment(env_id: int, user_id: int = Depends(require_auth)):
    env = store.get_environment(env_id, user_id)
    if not env:
        raise HTTPException(404, "Environment not found")
    stats = store.get_environment_stats(env_id, user_id)
    return {"id": env["id"], "name": env["name"], "created_at": env["created_at"], **stats}


@router.put(
    "/api/environments/{env_id}",
    responses={
        **ok({"id": 1, "name": "Home Lab"}),
        **ERR_401,
        **ERR_404,
        **ERR_400,
        **ERR_409,
    },
)
async def rename_environment(env_id: int, body: EnvRename, user_id: int = Depends(require_auth)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    env = store.get_environment(env_id, user_id)
    if not env:
        raise HTTPException(404, "Environment not found")
    try:
        store.rename_environment(env_id, user_id, name)
    except store.DuplicateError:
        raise HTTPException(409, "An environment with that name already exists")
    return {"id": env_id, "name": name}


@router.delete(
    "/api/environments/{env_id}",
    status_code=204,
    responses={**ERR_401, **ERR_404, **ERR_400},
)
async def delete_environment(env_id: int, user_id: int = Depends(require_auth)):
    # Prevent deleting the only environment
    envs = store.list_environments(user_id)
    if len(envs) <= 1:
        raise HTTPException(400, "Cannot delete the only environment")
    deleted, crops, sources = store.delete_environment(env_id, user_id)
    if not deleted:
        raise HTTPException(404, "Environment not found")
    delete_crops(crops)
    delete_sources(sources)
    gc_source_files()
    from app.core import face_index as _fi
    _fi.clear_environment(user_id, env_id)
