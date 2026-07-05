"""Tests for POST /api/export and POST /api/import."""

from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["DATA_PATH"] = str(tmp_path)
    os.environ["SECRET_KEY"] = "test-secret"
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("DATA_PATH", None)
    os.environ.pop("SECRET_KEY", None)


def _create_user_and_key(username: str = "tester") -> tuple[int, str]:
    from app.core.security import hash_password
    user_id = store.create_user(username, hash_password("pass"))
    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), "key")
    return user_id, plaintext


def _make_zip(identities: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("argus_export.json", json.dumps({
            "version": "0.1.0",
            "exported_at": "2025-01-01T00:00:00+00:00",
            "identities": identities,
        }))
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_requires_auth(client):
    r = client.post("/api/export", json={"identity_ids": [1]})
    assert r.status_code in (401, 403)


def test_export_empty_ids_400(client):
    _, key = _create_user_and_key()
    r = client.post("/api/export", json={"identity_ids": []}, headers={"X-API-Key": key})
    assert r.status_code == 400


def test_export_returns_zip(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    iid = store.create_identity(user_id, "face", "Alice", env_id)
    r = client.post("/api/export", json={"identity_ids": [iid]}, headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "argus_export.json" in zf.namelist()
    data = json.loads(zf.read("argus_export.json"))
    assert len(data["identities"]) == 1
    assert data["identities"][0]["label"] == "Alice"


def test_export_skips_other_users_identities(client):
    user_id1, key1 = _create_user_and_key("u1")
    user_id2, _ = _create_user_and_key("u2")
    env1 = store.get_default_environment_id(user_id1)
    env2 = store.get_default_environment_id(user_id2)
    store.create_identity(user_id1, "face", "Alice", env1)
    iid2 = store.create_identity(user_id2, "face", "Bob", env2)
    # user1 tries to export user2's identity — silently skipped
    r = client.post("/api/export", json={"identity_ids": [iid2]}, headers={"X-API-Key": key1})
    assert r.status_code == 200
    data = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("argus_export.json"))
    assert data["identities"] == []


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def test_import_requires_auth(client):
    z = _make_zip([])
    r = client.post("/api/import", files={"file": ("x.zip", z, "application/zip")})
    assert r.status_code in (401, 403)


def test_import_bad_zip_400(client):
    _, key = _create_user_and_key()
    r = client.post("/api/import",
                    files={"file": ("x.zip", b"notazip", "application/zip")},
                    headers={"X-API-Key": key})
    assert r.status_code == 400


def test_import_missing_manifest_400(client):
    _, key = _create_user_and_key()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.json", "{}")
    buf.seek(0)
    r = client.post("/api/import",
                    files={"file": ("x.zip", buf.read(), "application/zip")},
                    headers={"X-API-Key": key})
    assert r.status_code == 400


def test_import_creates_identity(client):
    _, key = _create_user_and_key()
    z = _make_zip([{"type": "face", "label": "Imported Person",
                    "embeddings": [], "detections": []}])
    r = client.post("/api/import",
                    files={"file": ("argus_export.zip", z, "application/zip")},
                    headers={"X-API-Key": key})
    assert r.status_code == 200
    stats = r.json()
    assert stats["identities_created"] == 1
    assert stats["identities_merged"] == 0


def test_import_merges_existing_identity(client):
    user_id, key = _create_user_and_key()
    env_id = store.get_default_environment_id(user_id)
    store.create_identity(user_id, "face", "Existing", env_id)
    z = _make_zip([{"type": "face", "label": "Existing",
                    "embeddings": [], "detections": []}])
    r = client.post("/api/import",
                    files={"file": ("argus_export.zip", z, "application/zip")},
                    headers={"X-API-Key": key})
    assert r.status_code == 200
    stats = r.json()
    assert stats["identities_created"] == 0
    assert stats["identities_merged"] == 1


def test_import_idempotent(client):
    _, key = _create_user_and_key()
    z = _make_zip([{"type": "face", "label": "Bob",
                    "embeddings": [], "detections": []}])
    client.post("/api/import", files={"file": ("x.zip", z, "application/zip")},
                headers={"X-API-Key": key})
    r2 = client.post("/api/import", files={"file": ("x.zip", z, "application/zip")},
                     headers={"X-API-Key": key})
    assert r2.status_code == 200
    assert r2.json()["identities_created"] == 0
    assert r2.json()["identities_merged"] == 1
