"""Tests for GET /api/images/{id}/faces, POST /api/images/{id}/tag, manual detection endpoints."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

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


def _insert_source(user_id: int, filename: str = "photo.jpg") -> int:
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO source_images (user_id, environment_id, file_path, width, height)
               VALUES (?, ?, ?, 1920, 1080)""",
            (user_id, env_id, filename),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# GET /api/source-images — justified Images page backend
# ---------------------------------------------------------------------------

def test_source_images_lists_with_shape(client):
    user_id, h = _setup(client)
    _insert_source(user_id, "a.jpg")
    _insert_source(user_id, "b.jpg")

    r = client.get("/api/source-images", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert {"items", "next_cursor", "has_more"} <= data.keys()
    assert len(data["items"]) == 2
    item = data["items"][0]
    assert {"source_image_id", "source_image_url", "width", "height",
            "detection_count", "uploaded_at"} <= item.keys()
    assert item["source_image_url"].startswith("/media/sources/")


def test_source_images_no_duplicates_on_reprocess(client):
    user_id, h = _setup(client)
    # Same content hash (file_path) ingested twice resolves to one row.
    id1 = store.get_or_create_source_image(user_id, "dup.jpg", 800, 600)
    id2 = store.get_or_create_source_image(user_id, "dup.jpg", 800, 600)
    assert id1 == id2

    items = client.get("/api/source-images", headers=h).json()["items"]
    assert len(items) == 1


def test_source_images_pagination_no_overlap(client):
    user_id, h = _setup(client)
    for i in range(5):
        _insert_source(user_id, f"img{i}.jpg")

    p1 = client.get("/api/source-images?limit=2", headers=h).json()
    assert p1["has_more"] is True and len(p1["items"]) == 2

    p2 = client.get(f"/api/source-images?limit=2&cursor={p1['next_cursor']}", headers=h).json()
    ids1 = {it["source_image_id"] for it in p1["items"]}
    ids2 = {it["source_image_id"] for it in p2["items"]}
    assert ids1.isdisjoint(ids2)  # no repeats across pages despite uploaded_at ties


def test_source_images_requires_auth(client):
    r = client.get("/api/source-images")
    assert r.status_code in (401, 403)


def _insert_face(user_id: int, src_id: int, label: str | None = None,
                 bbox: tuple = (10, 20, 100, 100), conf: float = 0.9) -> int:
    env_id = store.get_default_environment_id(user_id) or 0
    identity_id = None
    if label:
        identity_id, _ = store.get_or_create_identity(user_id, "face", label)
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, ?, ?, 'face', NULL, ?, ?, ?, ?, ?, 'crop.jpg')""",
            (user_id, env_id, identity_id, src_id, conf, *bbox),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# GET /api/images/{id}/faces
# ---------------------------------------------------------------------------

def test_image_faces_not_found(client):
    _, h = _setup(client)
    r = client.get("/api/images/999/faces", headers=h)
    assert r.status_code == 404


def test_image_faces_empty(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    assert r.status_code == 200
    assert r.json()["faces"] == []
    assert r.json()["width"] == 1920
    assert r.json()["height"] == 1080


def test_image_faces_returns_detections(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id, label="Noah", bbox=(10, 20, 100, 100))
    d2 = _insert_face(user_id, src_id, bbox=(200, 50, 80, 80))

    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    data = r.json()
    assert len(data["faces"]) == 2

    face_ids = {f["detection_id"] for f in data["faces"]}
    assert {d1, d2} == face_ids

    labeled = next(f for f in data["faces"] if f["detection_id"] == d1)
    assert labeled["label"] == "Noah"
    assert labeled["bbox"] == {"x": 10, "y": 20, "w": 100, "h": 100}

    unlabeled = next(f for f in data["faces"] if f["detection_id"] == d2)
    assert unlabeled["identity_id"] is None


def test_image_faces_excludes_objects(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    _insert_face(user_id, src_id)  # face
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:  # object
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, NULL, ?, 'object', NULL, 0.8, 0, 0, 50, 50, 'obj.jpg')""",
            (user_id, env_id, src_id),
        )
    r = client.get(f"/api/images/{src_id}/faces", headers=h)
    assert len(r.json()["faces"]) == 1


def test_image_faces_not_accessible_by_other_user(client):
    from app.core.security import hash_password
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)
    _insert_face(user_id, src_id)

    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")
    h2 = {"X-API-Key": k2}

    r = client.get(f"/api/images/{src_id}/faces", headers=h2)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/images/{id}
# ---------------------------------------------------------------------------

def test_delete_source_image_not_found(client):
    _, h = _setup(client)
    r = client.delete("/api/images/999", headers=h)
    assert r.status_code == 404


def test_delete_source_image_cascades_detections(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    _insert_face(user_id, src_id, label="Noah")
    env_id = store.get_default_environment_id(user_id) or 0
    with store._connect() as conn:  # add an object detection too
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path)
               VALUES (?, ?, NULL, ?, 'object', NULL, 0.8, 0, 0, 50, 50, 'obj.jpg')""",
            (user_id, env_id, src_id),
        )

    r = client.delete(f"/api/images/{src_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["detections_deleted"] == 2

    # Source image and its detections are gone
    assert store.get_source_image(src_id, user_id) is None
    assert store.get_image_detections(src_id, user_id) == []


def test_delete_source_image_not_accessible_by_other_user(client):
    from app.core.security import hash_password
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)

    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")
    h2 = {"X-API-Key": k2}

    r = client.delete(f"/api/images/{src_id}", headers=h2)
    assert r.status_code == 404
    # Still exists for the owner
    assert store.get_source_image(src_id, user_id) is not None


# ---------------------------------------------------------------------------
# POST /api/images/{id}/tag
# ---------------------------------------------------------------------------

def test_tag_labels_faces(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id)
    d2 = _insert_face(user_id, src_id)
    iid, _ = store.get_or_create_identity(user_id, "face", "Noah")

    r = client.post(f"/api/images/{src_id}/tag", json=[
        {"detection_id": d1, "identity_id": iid},
        {"detection_id": d2, "label": "Sarah"},
    ], headers=h)

    assert r.status_code == 200
    results = {item["detection_id"]: item for item in r.json()}
    assert results[d1]["label"] == "Noah"
    assert results[d2]["label"] == "Sarah"
    assert results[d1]["status"] == "labeled"


def test_tag_creates_identity_from_label(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    d1 = _insert_face(user_id, src_id)

    client.post(f"/api/images/{src_id}/tag", json=[
        {"detection_id": d1, "label": "BrandNew"},
    ], headers=h)

    identities = client.get("/api/identities", headers=h).json()["items"]
    assert any(i["label"] == "BrandNew" for i in identities)


def test_tag_wrong_image_detection_returns_not_found(client):
    user_id, h = _setup(client)
    src1 = _insert_source(user_id, "photo1.jpg")
    src2 = _insert_source(user_id, "photo2.jpg")
    d_on_src2 = _insert_face(user_id, src2)

    r = client.post(f"/api/images/{src1}/tag", json=[
        {"detection_id": d_on_src2, "label": "X"},
    ], headers=h)

    assert r.json()[0]["status"] == "not_found"


# ---------------------------------------------------------------------------
# GET /api/images/{id}/url
# ---------------------------------------------------------------------------

def test_source_image_url_returns_url(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id, "photo.jpg")
    r = client.get(f"/api/images/{src_id}/url", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "image_url" in data
    assert data["image_url"] == "/media/sources/photo.jpg"


def test_source_image_url_not_found(client):
    _, h = _setup(client)
    r = client.get("/api/images/999/url", headers=h)
    assert r.status_code == 404


def test_source_image_url_requires_auth(client):
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)
    r = client.get(f"/api/images/{src_id}/url")
    assert r.status_code in (401, 403)


def test_source_image_url_not_accessible_by_other_user(client):
    from app.core.security import hash_password
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)

    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")

    r = client.get(f"/api/images/{src_id}/url", headers={"X-API-Key": k2})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Helpers for manual-detection tests
# ---------------------------------------------------------------------------

def _mock_image(width: int = 1920, height: int = 1080) -> MagicMock:
    """Return a MagicMock Pillow Image with realistic dimensions."""
    img = MagicMock()
    img.width = width
    img.height = height
    img.mode = "RGB"
    return img


def _manual_patches(tmp_path, filename: str = "photo.jpg"):
    """Context manager stack that stubs out image I/O and heavy ML calls."""
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / filename).write_bytes(b"placeholder")
    return (
        patch("app.core.image_input.open_and_validate", return_value=_mock_image()),
        patch("app.api.detect._save_crop", return_value="crop_test.jpg"),
        patch("app.inference.runner.infer_faces", return_value=([], None)),
        patch("app.inference.runner.infer_face_embedding", return_value=None),
        patch("app.core.face_index.rebuild_user"),
    )


# ---------------------------------------------------------------------------
# POST /api/images/{id}/detections  (manual bbox)
# ---------------------------------------------------------------------------

def test_create_manual_detection_returns_201(client, tmp_path):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)  # default 1920x1080 in DB
    payload = {"bbox": {"x": 10, "y": 10, "w": 30, "h": 30}, "label": "Noah"}
    p1, p2, p3, p4, p5 = _manual_patches(tmp_path)
    with p1, p2, p3, p4, p5:
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    data = r.json()
    assert data["source"] == "manual"
    assert data["label"] == "Noah"
    assert data["bbox"] == {"x": 10, "y": 10, "w": 30, "h": 30}
    assert "detection_id" in data
    assert "identity_id" in data
    assert "crop_url" in data


def test_create_manual_detection_no_label_returns_400(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    r = client.post(
        f"/api/images/{src_id}/detections",
        json={"bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
        headers=h,
    )
    assert r.status_code == 400


def test_create_manual_detection_bbox_out_of_bounds_returns_400(client):
    user_id, h = _setup(client)
    # _insert_source default is 1920x1080; bbox x=1900 w=100 → 2000 > 1920
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 1900, "y": 10, "w": 100, "h": 30}, "label": "Noah"}
    r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 400


def test_create_manual_detection_zero_bbox_returns_400(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    r = client.post(
        f"/api/images/{src_id}/detections",
        json={"bbox": {"x": 0, "y": 0, "w": 0, "h": 10}, "label": "Noah"},
        headers=h,
    )
    assert r.status_code == 400


def test_create_manual_detection_unknown_source_image(client):
    _, h = _setup(client)
    r = client.post(
        "/api/images/999/detections",
        json={"bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "label": "Noah"},
        headers=h,
    )
    assert r.status_code == 404


def test_create_manual_detection_persists_source_field(client, tmp_path):
    """DB row must have source='manual' so the tag page can show dashed border."""
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 0, "y": 0, "w": 20, "h": 20}, "label": "Alice"}
    p1, p2, p3, p4, p5 = _manual_patches(tmp_path)
    with p1, p2, p3, p4, p5:
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    det_id = r.json()["detection_id"]
    with store._connect() as conn:
        row = conn.execute("SELECT source FROM detections WHERE id = ?", (det_id,)).fetchone()
    assert row["source"] == "manual"


def test_create_manual_detection_tier1_embedding(client, tmp_path):
    """Tier-1: RetinaFace finds a face in the crop → embedding_source='aligned'."""
    from app.inference.face_engine import FaceDetection
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 0, "y": 0, "w": 20, "h": 20}, "label": "Alice"}
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "photo.jpg").write_bytes(b"placeholder")
    fake_face = FaceDetection(bbox=(0, 0, 20, 20), confidence=0.95, embedding=MagicMock())
    fake_bytes = b"\x00" * (512 * 4)
    with patch("app.core.image_input.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect._save_crop", return_value="crop_test.jpg"), \
         patch("app.inference.runner.infer_faces", return_value=([fake_face], None)), \
         patch("app.api.detect._embedding_to_bytes", return_value=fake_bytes), \
         patch("app.core.face_index.rebuild_user"):
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    assert r.json()["embedding_source"] == "aligned"


def test_create_manual_detection_tier2_embedding(client, tmp_path):
    """Tier-2 fallback: when infer_faces finds nothing, ArcFace is called directly → embedding_source='raw'."""
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 0, "y": 0, "w": 20, "h": 20}, "label": "Alice"}
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "photo.jpg").write_bytes(b"placeholder")
    # Stub _embedding_to_bytes at its source module to bypass numpy (mocked in conftest).
    fake_feat = MagicMock()
    fake_bytes = b"\x00" * (512 * 4)
    with patch("app.core.image_input.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect._save_crop", return_value="crop_test.jpg"), \
         patch("app.inference.runner.infer_faces", return_value=([], None)), \
         patch("app.inference.runner.infer_face_embedding", return_value=fake_feat), \
         patch("app.api.detect._embedding_to_bytes", return_value=fake_bytes), \
         patch("app.core.face_index.rebuild_user"):
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    assert r.json()["embedding_source"] == "raw"


def test_create_manual_detection_tier3_no_embedding(client, tmp_path):
    """Tier-3 fallback: when both infer_faces and infer_face_embedding fail → embedding_source=None."""
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 0, "y": 0, "w": 20, "h": 20}, "label": "Alice"}
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "photo.jpg").write_bytes(b"placeholder")
    with patch("app.core.image_input.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect._save_crop", return_value="crop_test.jpg"), \
         patch("app.inference.runner.infer_faces", return_value=([], None)), \
         patch("app.inference.runner.infer_face_embedding", return_value=None), \
         patch("app.core.face_index.rebuild_user"):
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    assert r.json()["embedding_source"] is None


def test_create_manual_detection_embedding_source_persisted(client, tmp_path):
    """embedding_source is written to the DB and returned by GET /api/images/{id}/faces."""
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    payload = {"bbox": {"x": 0, "y": 0, "w": 20, "h": 20}, "label": "Alice"}
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "photo.jpg").write_bytes(b"placeholder")
    fake_feat = MagicMock()
    fake_bytes = b"\x00" * (512 * 4)
    with patch("app.core.image_input.open_and_validate", return_value=_mock_image()), \
         patch("app.api.detect._save_crop", return_value="crop_test.jpg"), \
         patch("app.inference.runner.infer_faces", return_value=([], None)), \
         patch("app.inference.runner.infer_face_embedding", return_value=fake_feat), \
         patch("app.api.detect._embedding_to_bytes", return_value=fake_bytes), \
         patch("app.core.face_index.rebuild_user"):
        r = client.post(f"/api/images/{src_id}/detections", json=payload, headers=h)
    assert r.status_code == 201
    det_id = r.json()["detection_id"]

    # Check DB row
    with store._connect() as conn:
        row = conn.execute(
            "SELECT embedding_source FROM detections WHERE id = ?", (det_id,)
        ).fetchone()
    assert row["embedding_source"] == "raw"

    # Check GET endpoint includes embedding_source
    r2 = client.get(f"/api/images/{src_id}/faces", headers=h)
    assert r2.status_code == 200
    face = next(f for f in r2.json()["faces"] if f["detection_id"] == det_id)
    assert face["embedding_source"] == "raw"


# ---------------------------------------------------------------------------
# DELETE /api/detections/{id}
# ---------------------------------------------------------------------------

def _insert_manual_detection(user_id: int, src_id: int) -> int:
    env_id = store.get_default_environment_id(user_id) or 0
    iid, _ = store.get_or_create_identity(user_id, "face", "TestPerson")
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO detections
               (user_id, environment_id, identity_id, source_image_id, type, model_id, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h, crop_path, source)
               VALUES (?, ?, ?, ?, 'face', NULL, 0.0, 10, 10, 30, 30, 'crop.jpg', 'manual')""",
            (user_id, env_id, iid, src_id),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_delete_detection_removes_row(client):
    user_id, h = _setup(client)
    src_id = _insert_source(user_id)
    det_id = _insert_manual_detection(user_id, src_id)
    with patch("app.core.face_index.rebuild_user"):
        r = client.delete(f"/api/detections/{det_id}", headers=h)
    assert r.status_code == 204
    with store._connect() as conn:
        row = conn.execute("SELECT id FROM detections WHERE id = ?", (det_id,)).fetchone()
    assert row is None


def test_delete_detection_not_found(client):
    _, h = _setup(client)
    r = client.delete("/api/detections/99999", headers=h)
    assert r.status_code == 404


def test_delete_detection_not_accessible_by_other_user(client):
    from app.core.security import hash_password
    user_id, _ = _setup(client)
    src_id = _insert_source(user_id)
    det_id = _insert_manual_detection(user_id, src_id)

    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")

    with patch("app.core.face_index.rebuild_user"):
        r = client.delete(f"/api/detections/{det_id}", headers={"X-API-Key": k2})
    assert r.status_code == 404
    with store._connect() as conn:
        row = conn.execute("SELECT id FROM detections WHERE id = ?", (det_id,)).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# has_manual_detections filter
# ---------------------------------------------------------------------------

def test_has_manual_detections_filter_returns_only_manual_images(client):
    user_id, h = _setup(client)
    src_manual = _insert_source(user_id, "manual.jpg")
    src_auto = _insert_source(user_id, "auto.jpg")

    _insert_manual_detection(user_id, src_manual)
    _insert_face(user_id, src_auto, label="Alice")

    r = client.get("/api/source-images?has_manual_detections=true", headers=h)
    assert r.status_code == 200
    ids = [it["source_image_id"] for it in r.json()["items"]]
    assert src_manual in ids
    assert src_auto not in ids


def test_has_manual_detections_filter_includes_mixed_images(client):
    """Image with both auto and manual detections must appear in the filter."""
    user_id, h = _setup(client)
    src_mixed = _insert_source(user_id, "mixed.jpg")

    _insert_face(user_id, src_mixed, label="Alice")
    _insert_manual_detection(user_id, src_mixed)

    r = client.get("/api/source-images?has_manual_detections=true", headers=h)
    assert r.status_code == 200
    ids = [it["source_image_id"] for it in r.json()["items"]]
    assert src_mixed in ids


def test_has_manual_detections_filter_excludes_auto_only_images(client):
    user_id, h = _setup(client)
    src_auto = _insert_source(user_id, "auto.jpg")
    _insert_face(user_id, src_auto, label="Alice")

    r = client.get("/api/source-images?has_manual_detections=true", headers=h)
    assert r.status_code == 200
    ids = [it["source_image_id"] for it in r.json()["items"]]
    assert src_auto not in ids


def test_has_manual_detections_count(client):
    user_id, h = _setup(client)
    src_manual = _insert_source(user_id, "manual.jpg")
    src_auto = _insert_source(user_id, "auto.jpg")

    _insert_manual_detection(user_id, src_manual)
    _insert_face(user_id, src_auto, label="Alice")

    r = client.get("/api/source-images/count?has_manual_detections=true", headers=h)
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_has_manual_detections_ids(client):
    user_id, h = _setup(client)
    src_manual = _insert_source(user_id, "manual.jpg")
    src_auto = _insert_source(user_id, "auto.jpg")

    _insert_manual_detection(user_id, src_manual)
    _insert_face(user_id, src_auto, label="Alice")

    r = client.get("/api/source-images/ids?has_manual_detections=true", headers=h)
    assert r.status_code == 200
    ids = r.json()["ids"]
    assert src_manual in ids
    assert src_auto not in ids


def test_has_manual_detections_isolated_per_user(client):
    """Another user's manual detections don't affect the current user's filter."""
    from app.core.security import hash_password
    user1, h1 = _setup(client)
    user2 = store.create_user("bob", hash_password("pass12345"))
    k2 = generate_api_key()
    store.create_api_key(user2, hash_api_key(k2), "b")
    h2 = {"X-API-Key": k2}

    src1 = _insert_source(user1, "img.jpg")
    env2 = store.get_default_environment_id(user2) or 0
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO source_images"
            " (user_id, environment_id, file_path, width, height) VALUES (?, ?, ?, 1920, 1080)",
            (user2, env2, "img.jpg"),
        )
        src2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    _insert_face(user1, src1, label="Alice")
    _insert_manual_detection(user2, src2)

    r1 = client.get("/api/source-images?has_manual_detections=true", headers=h1)
    assert r1.status_code == 200
    assert r1.json()["items"] == []

    r2 = client.get("/api/source-images?has_manual_detections=true", headers=h2)
    assert r2.status_code == 200
    ids2 = [it["source_image_id"] for it in r2.json()["items"]]
    assert src2 in ids2
