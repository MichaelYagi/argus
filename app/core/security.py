"""Password hashing and API key generation — stdlib only, no extra deps."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

# ---------------------------------------------------------------------------
# Passwords (scrypt via stdlib)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a storable scrypt hash string."""
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt:{salt.hex()}:{key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a plaintext password against a stored hash. Constant-time compare."""
    try:
        _, salt_hex, key_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def generate_api_key() -> str:
    """Return a new plaintext API key. Shown once — never stored."""
    return f"argus_{secrets.token_hex(32)}"


def hash_api_key(key: str) -> str:
    """Return the SHA-256 hex digest used for DB storage."""
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Remember-me tokens (signed with itsdangerous, already a Starlette dep)
# ---------------------------------------------------------------------------

REMEMBER_MAX_AGE = 30 * 24 * 3600  # 30 days


def create_remember_token(user_id: int, secret_key: str) -> str:
    from itsdangerous import TimestampSigner
    signer = TimestampSigner(secret_key)
    return signer.sign(str(user_id)).decode()


def verify_remember_token(token: str, secret_key: str) -> int | None:
    from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
    signer = TimestampSigner(secret_key)
    try:
        value = signer.unsign(token, max_age=REMEMBER_MAX_AGE)
        return int(value)
    except (SignatureExpired, BadSignature, ValueError):
        return None
