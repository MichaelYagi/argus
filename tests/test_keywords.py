"""Tests for CLIP keyword tagging: vocabulary, storage, index scoring, and API."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core import keyword_index
from app.core.security import generate_api_key, hash_api_key, hash_password
from app.db import store
from app.main import app

# conftest stubs numpy with a MagicMock in the minimal dev install; the numeric
# tests below only run against a real numpy (CI / full install).
_REAL_NUMPY = not isinstance(np, MagicMock)
_needs_numpy = pytest.mark.skipif(not _REAL_NUMPY, reason="numpy is stubbed in this install")


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)


def _admin(client) -> dict:
    uid = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    key = generate_api_key()
    store.create_api_key(uid, hash_api_key(key), "test")
    return {"X-API-Key": key}


def _non_admin(client) -> dict:
    uid = store.create_user("bob", hash_password("pass12345"), is_admin=False)
    key = generate_api_key()
    store.create_api_key(uid, hash_api_key(key), "test")
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# Store: vocabulary
# ---------------------------------------------------------------------------

def test_vocabulary_replace_dedups_and_bumps_version(tmp_path):
    store.configure(tmp_path / "t.db")
    store.init_db()
    assert store.get_vocab_version() == 1
    n = store.replace_vocabulary(["Dog", "dog", "  ", "Christmas", "birthday party"])
    assert n == 3
    assert store.get_vocabulary() == ["Christmas", "Dog", "birthday party"]
    assert store.get_vocab_version() == 2
    store.configure(None)


def test_default_vocabulary_seeded(tmp_path):
    store.configure(tmp_path / "t.db")
    store.init_db()
    # A curated default ships so tagging works out of the box.
    assert store.vocabulary_count() > 500
    vocab = {w.lower() for w in store.get_vocabulary()}
    assert "christmas" in vocab
    assert "birthday party" in vocab  # multi-word phrases included
    assert store.get_vocab_version() == 1  # seeded content is version 1
    store.configure(None)


def test_bump_vocab_version(tmp_path):
    store.configure(tmp_path / "t.db")
    store.init_db()
    v0 = store.get_vocab_version()
    assert store.bump_vocab_version() == v0 + 1
    store.configure(None)


# ---------------------------------------------------------------------------
# Store: embeddings + keywords + search
# ---------------------------------------------------------------------------

@_needs_numpy
def test_image_embedding_and_keyword_storage(tmp_path):
    store.configure(tmp_path / "t.db")
    store.init_db()
    uid = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    sid = store.get_or_create_source_image(uid, "abc.jpg", 100, 100)
    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    store.upsert_image_embedding(sid, uid, model_id=1, embedding=vec.tobytes(), dim=3)
    got = store.get_image_embedding(sid, 1)
    assert np.frombuffer(got, dtype=np.float32).tolist() == pytest.approx([0.1, 0.2, 0.3])

    store.set_image_keywords(sid, uid, 1, 2, [("dog", 0.9), ("beach", 0.6)])
    rows = store.get_image_keywords(sid, 1)
    assert [r["keyword"] for r in rows] == ["dog", "beach"]

    # Re-setting replaces, not appends.
    store.set_image_keywords(sid, uid, 1, 3, [("cat", 0.8)])
    rows = store.get_image_keywords(sid, 1)
    assert [r["keyword"] for r in rows] == ["cat"]
    store.configure(None)


def test_search_images_by_keyword(tmp_path):
    store.configure(tmp_path / "t.db")
    store.init_db()
    uid = store.create_user("alice", hash_password("pass12345"), is_admin=True)
    s1 = store.get_or_create_source_image(uid, "a.jpg", 10, 10)
    s2 = store.get_or_create_source_image(uid, "b.jpg", 10, 10)
    store.set_image_keywords(s1, uid, 1, 1, [("Christmas", 0.7)])
    store.set_image_keywords(s2, uid, 1, 1, [("beach", 0.7)])
    rows = store.search_images_by_keyword(uid, "christmas", model_id=1)
    assert [r["source_image_id"] for r in rows] == [s1]
    store.configure(None)


# ---------------------------------------------------------------------------
# Index scoring (real numpy, injected matrix)
# ---------------------------------------------------------------------------

@_needs_numpy
def test_index_score_top_k_and_threshold():
    keyword_index._words = ["dog", "cat", "beach"]
    keyword_index._matrix = np.eye(3, dtype=np.float32)
    keyword_index._model_id = 1
    keyword_index._vocab_version = 1
    try:
        vec = np.array([0.9, 0.2, 0.0], dtype=np.float32)
        res = keyword_index.score(vec, top_k=2, threshold=0.1)
        labels = [w for w, _ in res]
        assert labels[0] == "dog"
        assert "beach" not in labels  # zero similarity, below threshold
        # Threshold filters everything out.
        assert keyword_index.score(vec, top_k=3, threshold=0.99) == []
    finally:
        keyword_index.reset()


def test_index_score_empty_when_no_matrix():
    keyword_index.reset()
    assert keyword_index.score(np.array([1.0, 0.0], dtype=np.float32)) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_vocabulary_api_admin_only(client):
    h = _non_admin(client)
    assert client.get("/api/keywords/vocabulary", headers=h).status_code == 403
    assert client.put("/api/keywords/vocabulary", json={"words": ["x"]}, headers=h).status_code == 403


def test_vocabulary_api_put_get_dedups(client):
    h = _admin(client)
    r = client.put("/api/keywords/vocabulary", json={"words": ["Dog", "dog", "Cat"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["count"] == 2
    g = client.get("/api/keywords/vocabulary", headers=h)
    assert g.status_code == 200
    assert set(g.json()["words"]) == {"Dog", "Cat"}


def test_keywords_endpoint_409_without_model(client):
    h = _admin(client)
    r = client.post("/api/keywords", json={"image_base64": "x"}, headers=h)
    assert r.status_code == 409


def test_stored_keywords_empty_without_model(client):
    h = _admin(client)
    r = client.get("/api/images/1/keywords", headers=h)
    assert r.status_code == 200
    assert r.json()["keywords"] == []


def test_search_empty_without_model(client):
    h = _admin(client)
    r = client.get("/api/images/search?keyword=dog", headers=h)
    assert r.status_code == 200
    assert r.json()["items"] == []


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

def test_clip_settings_validation(client):
    h = _admin(client)
    assert client.put("/api/settings/clip.tag_top_k", json={"value": 0}, headers=h).status_code == 400
    assert client.put("/api/settings/clip.tag_top_k", json={"value": 200}, headers=h).status_code == 400
    assert client.put("/api/settings/clip.tag_top_k", json={"value": 10}, headers=h).status_code == 200
    assert client.put("/api/settings/clip.tag_threshold", json={"value": 2}, headers=h).status_code == 400
    assert client.put("/api/settings/clip.tag_threshold", json={"value": 0.3}, headers=h).status_code == 200
    assert client.put("/api/settings/clip.prompt_template", json={"value": "no placeholder"},
                      headers=h).status_code == 400
    assert client.put("/api/settings/clip.prompt_template", json={"value": "a photo of {word}"},
                      headers=h).status_code == 200
