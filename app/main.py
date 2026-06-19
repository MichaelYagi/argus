"""FastAPI application — single app object used by both Docker and `python -m app`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app import __version__
from app.api import health
from app.core import settings_cache
from app.db import store


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    store.init_db()
    settings_cache.cache.load()
    yield


app = FastAPI(title="Argus", version=__version__, lifespan=lifespan)
app.include_router(health.router)
