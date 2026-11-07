"""Password hashing + session tokens (issue #29).

Stdlib only: PBKDF2-HMAC-SHA256 with per-password salt. Session
tokens are 48-byte urlsafe secrets — the cookie carries the token
while the server holds the row (revocable).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

_ITER = 210_000
SESSION_HOURS = 12
SESSION_COOKIE = "ff_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, _ITER)
    return ("pbkdf2$" + base64.urlsafe_b64encode(salt).decode()
            + "$" + base64.urlsafe_b64encode(dk).decode())


def verify_password(password: str, stored: str | None) -> bool:
    if stored is None or not stored.startswith("pbkdf2$"):
        return False
    try:
        _, salt_b64, dk_b64 = stored.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_b64)
        want = base64.urlsafe_b64decode(dk_b64)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, _ITER)
    return hmac.compare_digest(got, want)


def new_token() -> str:
    return secrets.token_urlsafe(48)


def session_expiry() -> datetime:
    return datetime.now() + timedelta(hours=SESSION_HOURS)
