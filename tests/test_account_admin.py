"""Account self-service deletion and admin user management."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import store
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["SECRET_KEY"] = "test-secret"
    store.configure(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    store.configure(None)
    os.environ.pop("SECRET_KEY", None)


def _mk(username: str, is_admin: bool = False, is_approved: bool = True) -> int:
    return store.create_user(username, hash_password("pass12345"),
                             is_admin=is_admin, is_approved=is_approved)


def _login(client, username: str) -> None:
    r = client.post("/login", data={"username": username, "password": "pass12345"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)


# ---------------------------------------------------------------------------
# Self-service account deletion
# ---------------------------------------------------------------------------

def test_user_can_delete_own_account(client):
    _mk("admin", is_admin=True)
    uid = _mk("bob")
    _login(client, "bob")
    r = client.post("/account/delete", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert store.get_user_by_id(uid) is None


def test_deleting_account_cascades_data(client):
    _mk("admin", is_admin=True)
    uid = _mk("bob")
    store.create_identity(uid, "face", "Someone")
    _login(client, "bob")
    client.post("/account/delete", follow_redirects=False)
    assert store.count_identities(uid) == 0


def test_admin_cannot_delete_own_account(client):
    admin = _mk("admin", is_admin=True)
    _login(client, "admin")
    client.post("/account/delete", follow_redirects=False)
    assert store.get_user_by_id(admin) is not None


# ---------------------------------------------------------------------------
# Admin user management
# ---------------------------------------------------------------------------

def test_admin_can_revoke_and_grant_access(client):
    _mk("admin", is_admin=True)
    uid = _mk("bob", is_approved=True)
    _login(client, "admin")

    client.post(f"/admin/revoke/{uid}", follow_redirects=False)
    assert store.get_user_by_id(uid)["is_approved"] == 0

    client.post(f"/admin/approve/{uid}", follow_redirects=False)
    assert store.get_user_by_id(uid)["is_approved"] == 1


def test_admin_can_delete_user(client):
    _mk("admin", is_admin=True)
    uid = _mk("bob")
    _login(client, "admin")
    client.post(f"/admin/user/{uid}/delete", follow_redirects=False)
    assert store.get_user_by_id(uid) is None


def test_nonadmin_cannot_use_admin_routes(client):
    _mk("admin", is_admin=True)
    _mk("bob")
    victim = _mk("carol")
    _login(client, "bob")

    client.post(f"/admin/user/{victim}/delete", follow_redirects=False)
    assert store.get_user_by_id(victim) is not None

    client.post(f"/admin/revoke/{victim}", follow_redirects=False)
    assert store.get_user_by_id(victim)["is_approved"] == 1


# ---------------------------------------------------------------------------
# Store guards
# ---------------------------------------------------------------------------

def test_delete_user_never_deletes_admin(client):
    admin = _mk("admin", is_admin=True)
    assert store.delete_user(admin) is False
    assert store.get_user_by_id(admin) is not None


def test_set_user_approved_never_changes_admin(client):
    admin = _mk("admin", is_admin=True)
    assert store.set_user_approved(admin, False) is False
    assert store.get_user_by_id(admin)["is_approved"] == 1


def test_list_managed_users_excludes_self(client):
    admin = _mk("admin", is_admin=True)
    _mk("bob")
    rows = store.list_managed_users(admin)
    names = {r["username"] for r in rows}
    assert names == {"bob"}
