"""Tests for GET/DELETE /api/jobs and /api/jobs/{job_id}."""

from __future__ import annotations

import os
import uuid

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


def _seed_job(user_id: int, status: str = "done") -> str:
    job_id = str(uuid.uuid4())
    env_id = store.get_default_environment_id(user_id)
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, user_id, environment_id, type, status) VALUES (?, ?, ?, 'detect', ?)",
            (job_id, user_id, env_id, status),
        )
    return job_id


def test_jobs_requires_auth(client):
    r = client.get("/api/jobs")
    assert r.status_code in (401, 403)


def test_list_jobs_empty(client):
    _, key = _create_user_and_key()
    r = client.get("/api/jobs", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json() == []


def test_list_jobs_returns_seeded(client):
    user_id, key = _create_user_and_key()
    _seed_job(user_id, "done")
    r = client.get("/api/jobs", headers={"X-API-Key": key})
    assert r.status_code == 200
    jobs = r.json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "done"
    assert "job_id" in jobs[0]


def test_get_job(client):
    user_id, key = _create_user_and_key()
    job_id = _seed_job(user_id, "running")
    r = client.get(f"/api/jobs/{job_id}", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["job_id"] == job_id
    assert r.json()["status"] == "running"


def test_get_job_404(client):
    _, key = _create_user_and_key()
    r = client.get("/api/jobs/nonexistent-id", headers={"X-API-Key": key})
    assert r.status_code == 404


def test_delete_job(client):
    user_id, key = _create_user_and_key()
    job_id = _seed_job(user_id)
    r = client.delete(f"/api/jobs/{job_id}", headers={"X-API-Key": key})
    assert r.status_code == 204
    assert client.get(f"/api/jobs/{job_id}", headers={"X-API-Key": key}).status_code == 404


def test_delete_job_404(client):
    _, key = _create_user_and_key()
    r = client.delete("/api/jobs/nonexistent-id", headers={"X-API-Key": key})
    assert r.status_code == 404


def test_jobs_isolated_between_users(client):
    user_id1, key1 = _create_user_and_key("u1")
    _, key2 = _create_user_and_key("u2")
    job_id = _seed_job(user_id1)
    r = client.get(f"/api/jobs/{job_id}", headers={"X-API-Key": key2})
    assert r.status_code == 404
