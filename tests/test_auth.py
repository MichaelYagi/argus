"""Auth: sign up, sign in, sign out, remember-me, API key management."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_api_key, hash_api_key, verify_password
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret-key"
    store.configure(tmp_path / "test.db")
    with TestClient(app, follow_redirects=False) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)


# ---------------------------------------------------------------------------
# Sign up
# ---------------------------------------------------------------------------

def test_signup_creates_user(client):
    r = client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    assert r.status_code == 303
    row = store.get_user_by_username("alice")
    assert row is not None
    assert verify_password("password123", row["password_hash"])


def test_first_user_is_admin(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    assert store.get_user_by_username("alice")["is_admin"] == 1


def test_second_user_is_not_admin(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    client.post("/signup", data={"username": "bob", "password": "password456", "confirm": "password456"})
    assert store.get_user_by_username("bob")["is_admin"] == 0


def test_signup_duplicate_username_fails(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    r = client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    assert r.status_code == 200  # re-renders signup page with error
    assert store.count_users() == 1


def test_signup_password_mismatch_fails(client):
    r = client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "wrong"})
    assert r.status_code == 200
    assert store.count_users() == 0


def test_signup_short_password_fails(client):
    r = client.post("/signup", data={"username": "alice", "password": "short", "confirm": "short"})
    assert r.status_code == 200
    assert store.count_users() == 0


# ---------------------------------------------------------------------------
# Sign in / sign out
# ---------------------------------------------------------------------------

def test_login_valid_credentials(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    r = client.post("/login", data={"username": "alice", "password": "password123"})
    assert r.status_code == 303
    # alice is the first user (admin); with no models downloaded, admins land on /models.
    assert r.headers["location"] in ("/", "/account", "/models")


def test_login_invalid_password(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    r = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert r.status_code == 200  # re-renders login with error


def test_login_unknown_user(client):
    r = client.post("/login", data={"username": "nobody", "password": "password123"})
    assert r.status_code == 200


def test_logout_clears_session(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    client.post("/login", data={"username": "alice", "password": "password123"})
    r = client.post("/logout")
    assert r.status_code == 303


def test_remember_me_sets_cookie(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    r = client.post("/login", data={"username": "alice", "password": "password123", "remember": "1"})
    assert "argus_remember" in r.cookies


def test_no_remember_me_skips_cookie(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    r = client.post("/login", data={"username": "alice", "password": "password123"})
    assert "argus_remember" not in r.cookies


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

def _auth_header(client: TestClient) -> dict[str, str]:
    """Sign up, create an API key, return the auth header."""
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    user = store.get_user_by_username("alice")
    plaintext = generate_api_key()
    store.create_api_key(user["id"], hash_api_key(plaintext), "test key")
    return {"X-API-Key": plaintext}


def test_list_keys_empty_initially(client):
    headers = _auth_header(client)
    r = client.get("/api/keys", headers=headers)
    assert r.status_code == 200
    # First user gets an auto-generated "Default key" on signup + the "test key" we created
    assert len(r.json()) == 2


def test_create_key_returns_plaintext_once(client):
    headers = _auth_header(client)
    r = client.post("/api/keys", json={"label": "my script"}, headers=headers)
    assert r.status_code == 201
    data = r.json()
    assert data["key"].startswith("argus_")
    assert "label" in data


def test_revoke_key(client):
    headers = _auth_header(client)
    r = client.post("/api/keys", json={"label": "to revoke"}, headers=headers)
    key_id = r.json()["id"]
    r2 = client.delete(f"/api/keys/{key_id}", headers=headers)
    assert r2.status_code == 204


def test_revoke_other_users_key_fails(client):
    client.post("/signup", data={"username": "alice", "password": "password123", "confirm": "password123"})
    client.post("/signup", data={"username": "bob", "password": "password456", "confirm": "password456"})

    alice = store.get_user_by_username("alice")
    bob = store.get_user_by_username("bob")

    alice_key = generate_api_key()
    store.create_api_key(alice["id"], hash_api_key(alice_key), "alice key")

    bob_key = generate_api_key()
    bob_key_id = store.create_api_key(bob["id"], hash_api_key(bob_key), "bob key")

    # Alice tries to revoke Bob's key — should 404
    r = client.delete(f"/api/keys/{bob_key_id}", headers={"X-API-Key": alice_key})
    assert r.status_code == 404


def test_revoked_key_cannot_authenticate(client):
    headers = _auth_header(client)
    r = client.post("/api/keys", json={"label": "temp"}, headers=headers)
    new_key = r.json()["key"]
    new_id = r.json()["id"]

    client.delete(f"/api/keys/{new_id}", headers=headers)

    r2 = client.get("/api/keys", headers={"X-API-Key": new_key})
    assert r2.status_code == 403
