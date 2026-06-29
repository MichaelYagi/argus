"""Happy-path tests for review queue and casual correction."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["DATA_PATH"] = str(tmp_path)
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)
    os.environ.pop("DATA_PATH", None)


def _setup(client) -> tuple[int, dict]:
    from app.core.security import hash_password
    user_id = store.create_user("alice", hash_password("pass12345"))
    key = generate_api_key()
    store.create_api_key(user_id, hash_api_key(key), "test")
    return user_id, {"X-API-Key": key}


def _insert_detection(user_id: int, confidence: float = 0.5, identity_id: int | None = None,
                      det_type: str = "face") -> int:
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO source_images (user_id, environment_id, file_path, width, height) VALUES (?, ?, ?, 640, 480)",
            (user_id, env_id, f"src_{confidence}.jpg"),
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, ?, ?, ?, NULL, ?, 0, 0, 100, 100, 'crop.jpg')""",
            (user_id, env_id, identity_id, src_id, det_type, confidence),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def test_review_queue_empty(client):
    _, h = _setup(client)
    r = client.get("/api/review", headers=h)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_review_queue_returns_pending_faces(client):
    user_id, h = _setup(client)
    _insert_detection(user_id, 0.4)
    _insert_detection(user_id, 0.6)

    r = client.get("/api/review", headers=h)
    items = r.json()["items"]
    assert len(items) == 2
    # Lowest confidence first
    assert items[0]["confidence"] < items[1]["confidence"]


def test_review_queue_excludes_objects(client):
    user_id, h = _setup(client)
    _insert_detection(user_id, 0.5, det_type="object")
    _insert_detection(user_id, 0.4, det_type="face")

    r = client.get("/api/review", headers=h)
    assert len(r.json()["items"]) == 1


def test_review_queue_excludes_non_pending(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id, 0.4)
    store.confirm_detection(did, user_id)
    _insert_detection(user_id, 0.6)

    r = client.get("/api/review", headers=h)
    assert len(r.json()["items"]) == 1


def test_review_queue_pagination(client):
    user_id, h = _setup(client)
    for i in range(5):
        _insert_detection(user_id, 0.3 + i * 0.05)

    r = client.get("/api/review?limit=3", headers=h)
    data = r.json()
    assert len(data["items"]) == 3
    assert data["has_more"] is True

    r2 = client.get(f"/api/review?limit=3&cursor={data['next_cursor']}", headers=h)
    assert len(r2.json()["items"]) == 2
    assert r2.json()["has_more"] is False


# ---------------------------------------------------------------------------
# Confirm / reject / reassign
# ---------------------------------------------------------------------------

def test_confirm_detection(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id)
    r = client.post(f"/api/review/{did}/confirm", headers=h)
    assert r.status_code == 200
    assert r.json()["review_status"] == "confirmed"

    # No longer in review queue
    queue = client.get("/api/review", headers=h).json()
    assert queue["items"] == []


def test_reject_detection(client):
    user_id, h = _setup(client)
    identity_id = store.get_or_create_identity(user_id, "face", "Mike")
    did = _insert_detection(user_id, identity_id=identity_id)

    r = client.post(f"/api/review/{did}/reject", headers=h)
    assert r.status_code == 200
    assert r.json()["review_status"] == "rejected"

    det = store.get_detection(did, user_id)
    assert det["identity_id"] is None


def test_reassign_by_identity_id(client):
    user_id, h = _setup(client)
    iid = store.get_or_create_identity(user_id, "face", "Sarah")
    did = _insert_detection(user_id)

    r = client.post(f"/api/review/{did}/reassign", json={"identity_id": iid}, headers=h)
    assert r.status_code == 200
    assert r.json()["identity_id"] == iid
    assert r.json()["review_status"] == "reassigned"


def test_reassign_by_label_creates_identity(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id)

    r = client.post(f"/api/review/{did}/reassign", json={"label": "NewPerson"}, headers=h)
    assert r.status_code == 200
    assert r.json()["review_status"] == "reassigned"

    identities = client.get("/api/identities", headers=h).json()["items"]
    assert any(i["label"] == "NewPerson" for i in identities)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------

def test_bulk_review(client):
    user_id, h = _setup(client)
    d1 = _insert_detection(user_id, 0.4)
    d2 = _insert_detection(user_id, 0.5)
    d3 = _insert_detection(user_id, 0.6)

    r = client.post("/api/review/bulk", json=[
        {"detection_id": d1, "action": "confirm"},
        {"detection_id": d2, "action": "reject"},
        {"detection_id": d3, "action": "reassign", "label": "Alex"},
    ], headers=h)

    assert r.status_code == 200
    statuses = {item["detection_id"]: item["status"] for item in r.json()}
    assert statuses[d1] == "confirmed"
    assert statuses[d2] == "rejected"
    assert statuses[d3] == "reassigned"


# ---------------------------------------------------------------------------
# Casual correction — PUT /api/detections/{id}/label
# ---------------------------------------------------------------------------

def test_label_detection_by_identity_id(client):
    user_id, h = _setup(client)
    iid = store.get_or_create_identity(user_id, "face", "Mike")
    did = _insert_detection(user_id)

    r = client.put(f"/api/detections/{did}/label", json={"identity_id": iid}, headers=h)
    assert r.status_code == 200
    assert r.json()["identity_id"] == iid
    assert r.json()["label"] == "Mike"
    assert r.json()["review_status"] == "confirmed"


def test_label_detection_by_label(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id)

    r = client.put(f"/api/detections/{did}/label", json={"label": "Sarah"}, headers=h)
    assert r.status_code == 200
    assert r.json()["label"] == "Sarah"
    assert r.json()["review_status"] == "confirmed"


def test_label_detection_creates_new_identity(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id)

    client.put(f"/api/detections/{did}/label", json={"label": "BrandNew"}, headers=h)
    identities = client.get("/api/identities", headers=h).json()["items"]
    assert any(i["label"] == "BrandNew" for i in identities)


def test_label_detection_works_for_objects(client):
    user_id, h = _setup(client)
    did = _insert_detection(user_id, det_type="object")

    r = client.put(f"/api/detections/{did}/label", json={"label": "dog"}, headers=h)
    assert r.status_code == 200
    assert r.json()["review_status"] == "confirmed"


def test_label_detection_not_found(client):
    _, h = _setup(client)
    r = client.put("/api/detections/999/label", json={"label": "X"}, headers=h)
    assert r.status_code == 404
