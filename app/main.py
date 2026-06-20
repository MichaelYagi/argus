"""FastAPI application — single app object used by both Docker and `python -m app`."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api import detect, enroll, health, identities, images, keys, media, models, review, settings
from app.core import settings_cache
from app.db import store
from app.pages import account, auth, bulk, main_pages, tag
from app.pages import keys as keys_page

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SECRET_KEY", "change-me") == "change-me":
        log.warning("SECRET_KEY is not set — sessions are insecure. Set SECRET_KEY in .env.")
    store.init_db()
    settings_cache.cache.load()
    _autoload_engines()
    yield


def _autoload_engines() -> None:
    """Re-load whichever engines were active before this process started."""
    from app.core.engine_registry import registry
    from app.core.paths import models_dir

    face_row = store.get_active_model("face")
    if face_row and face_row["is_downloaded"]:
        try:
            from app.core.face_engine import FaceEngine
            registry.swap_face_engine(FaceEngine(face_row["name"], models_dir()))
            log.info("Loaded face model: %s", face_row["name"])
        except Exception as exc:
            log.warning("Failed to load face model %s: %s", face_row["name"], exc)

    obj_row = store.get_active_model("object")
    if obj_row and obj_row["is_downloaded"]:
        try:
            from app.core.object_engine import ObjectEngine
            path = models_dir() / f"{obj_row['name']}.pt"
            registry.swap_object_engine(ObjectEngine(obj_row["name"], path))
            log.info("Loaded object model: %s", obj_row["name"])
        except Exception as exc:
            log.warning("Failed to load object model %s: %s", obj_row["name"], exc)


app = FastAPI(title="Argus", version=__version__, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "change-me"),
    session_cookie="argus_session",
    https_only=False,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# API routes
app.include_router(health.router)
app.include_router(detect.router)
app.include_router(media.router)
app.include_router(keys.router)
app.include_router(identities.router)
app.include_router(enroll.router)
app.include_router(models.router)
app.include_router(settings.router)
app.include_router(review.router)
app.include_router(images.router)

# Page routes
app.include_router(main_pages.router)
app.include_router(bulk.router)
app.include_router(tag.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(keys_page.router)
