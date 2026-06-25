"""Tests for unsupervised face clustering (suggested people).

conftest stubs numpy for the suite, but clustering needs the real thing — so this
module swaps the real numpy in for its tests and restores the stub afterward.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.core import clustering
from app.core.security import generate_api_key, hash_api_key, hash_password
from app.db import store
from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _real_numpy():
    stub = sys.modules.pop("numpy", None)
    sys.modules["numpy"] = importlib.import_module("numpy")
    yield
    if stub is not None:
        sys.modules["numpy"] = stub
    else:
        sys.modules.pop("numpy", None)


def _vec(*xs) -> bytes:
    import numpy as np
    return np.array(xs, dtype=np.float32).tobytes()


# ---------------------------------------------------------------------------
# Core clustering
# ---------------------------------------------------------------------------

def test_cluster_groups_similar_and_drops_singletons():
    # Two tight groups along orthogonal axes, plus one lone outlier.
    items = [
        (1, _vec(1.0, 0.0)),
        (2, _vec(0.99, 0.01)),
        (3, _vec(0.0, 1.0)),
        (4, _vec(0.02, 0.99)),
        (5, _vec(0.71, 0.71)),  # ~45°, between both → singleton at high threshold
    ]
    clusters = clustering.cluster_embeddings(items, threshold=0.9, min_size=2)
    groups = sorted([sorted(c) for c in clusters])
    assert groups == [[1, 2], [3, 4]]  # the outlier (5) is dropped


def test_cluster_threshold_merges_when_loose():
    items = [(1, _vec(1.0, 0.0)), (2, _vec(0.71, 0.71)), (3, _vec(0.0, 1.0))]
    # Loose threshold chains all three together (1~2 and 2~3 both ~0.71).
    clusters = clustering.cluster_embeddings(items, threshold=0.7, min_size=2)
    assert len(clusters) == 1 and sorted(clusters[0]) == [1, 2, 3]


def test_cluster_empty_and_too_few():
    assert clustering.cluster_embeddings([], 0.5) == []
    assert clustering.cluster_embeddings([(1, _vec(1.0, 0.0))], 0.5, min_size=2) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _create_user_and_key(username: str = "tester") -> tuple[int, str]:
    user_id = store.create_user(username, hash_password("password123"))
    plaintext = generate_api_key()
    store.create_api_key(user_id, hash_api_key(plaintext), "test key")
    return user_id, plaintext


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


def _activate_face_model() -> int:
    with store._connect() as conn:
        row = conn.execute("SELECT id FROM models WHERE type='face' LIMIT 1").fetchone()
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (row[0],))
    return row[0]


def _insert_unknown_face(user_id: int, model_id: int, emb: bytes, label_crop: str) -> int:
    env_id = store.get_default_environment_id(user_id)
    source_id = store.get_or_create_source_image(user_id, label_crop, 100, 100, env_id)
    return store.insert_detection(
        user_id=user_id, identity_id=None, source_image_id=source_id,
        detection_type="face", model_id=model_id, confidence=0.9,
        bbox_x=1, bbox_y=2, bbox_w=3, bbox_h=4, crop_path=label_crop,
        embedding=emb, environment_id=env_id,
    )


def test_clusters_endpoint(client):
    user_id, key = _create_user_and_key()
    mid = _activate_face_model()
    a1 = _insert_unknown_face(user_id, mid, _vec(1.0, 0.0), "a1.jpg")
    a2 = _insert_unknown_face(user_id, mid, _vec(0.99, 0.02), "a2.jpg")
    _insert_unknown_face(user_id, mid, _vec(0.0, 1.0), "b1.jpg")  # lone → dropped

    r = client.get("/api/clusters?threshold=0.9", headers={"X-API-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert len(data["clusters"]) == 1
    c = data["clusters"][0]
    assert c["size"] == 2 and set(c["detection_ids"]) == {a1, a2}
    assert data["unclustered"] == 1


def test_clusters_naming_uses_batch_label(client):
    user_id, key = _create_user_and_key()
    mid = _activate_face_model()
    a1 = _insert_unknown_face(user_id, mid, _vec(1.0, 0.0), "a1.jpg")
    a2 = _insert_unknown_face(user_id, mid, _vec(0.99, 0.02), "a2.jpg")

    # Name the cluster via the batch-label endpoint (what the UI does).
    r = client.post("/api/detections/label", json={"items": [
        {"detection_id": a1, "label": "Noah"},
        {"detection_id": a2, "label": "Noah"},
    ]}, headers={"X-API-Key": key})
    assert r.status_code == 200

    # Both now belong to one identity; no unknowns remain to cluster.
    after = client.get("/api/clusters?threshold=0.9", headers={"X-API-Key": key}).json()
    assert after["clusters"] == [] and after["unclustered"] == 0


def test_clusters_no_active_model(client):
    _, key = _create_user_and_key()
    r = client.get("/api/clusters", headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["clusters"] == []


def test_clusters_requires_api_key(client):
    r = client.get("/api/clusters")
    assert r.status_code in (401, 403)


def test_dismiss_hides_from_clusters_but_keeps_row(client):
    user_id, key = _create_user_and_key()
    mid = _activate_face_model()
    a1 = _insert_unknown_face(user_id, mid, _vec(1.0, 0.0), "a1.jpg")
    a2 = _insert_unknown_face(user_id, mid, _vec(0.99, 0.02), "a2.jpg")

    r = client.post("/api/detections/dismiss", json={"detection_ids": [a1, a2]},
                    headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["dismissed"] == 2

    # Gone from Suggested...
    data = client.get("/api/clusters?threshold=0.9", headers={"X-API-Key": key}).json()
    assert data["clusters"] == [] and data["unclustered"] == 0
    # ...but the rows still exist.
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM detections WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert n == 2


def test_delete_removes_rows(client):
    user_id, key = _create_user_and_key()
    mid = _activate_face_model()
    a1 = _insert_unknown_face(user_id, mid, _vec(1.0, 0.0), "a1.jpg")
    a2 = _insert_unknown_face(user_id, mid, _vec(0.99, 0.02), "a2.jpg")

    r = client.post("/api/detections/delete", json={"detection_ids": [a1, a2]},
                    headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["deleted"] == 2

    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM detections WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert n == 0


def test_dismiss_requires_ids(client):
    _, key = _create_user_and_key()
    r = client.post("/api/detections/dismiss", json={"detection_ids": []},
                    headers={"X-API-Key": key})
    assert r.status_code == 400
