"""Encrypted credential vault (issue #30).

Server-side AES-256-GCM store. The rest of the system holds only
the opaque ``ref`` handle — ciphertext never leaves this module
unencrypted and values are never serialised to any API response,
log or diagnostic.

Key resolution order (never from ``options.json`` plaintext):

1. ``FF_VAULT_KEY`` env var (base64url 32 bytes) — the HA secrets
   path; when set it is the *current* generation.
2. ``<data_dir>/.vault_key`` — auto-generated 32-byte file, mode
   0600. Always present as generation 1 so env-key rotation has a
   prior generation to decrypt.

Each row's ``key_version`` records which generation produced it;
``rotate`` re-encrypts stale rows under the current key.
"""

from __future__ import annotations

import base64
import os
import secrets
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.persistence.models import VaultEntry

KEY_LEN = 32
NONCE_LEN = 12
KEY_FILE = ".vault_key"


class VaultError(ValueError):
    pass


class Keyring:
    """version → AESGCM instances; ``current`` is the highest."""

    def __init__(self, keys: dict[int, bytes]) -> None:
        if not keys:
            raise VaultError("keyring is empty")
        self._aeads = {v: AESGCM(k) for v, k in keys.items()}
        self.current = max(keys)

    def encrypt(self, plaintext: bytes) -> tuple[bytes, int]:
        nonce = secrets.token_bytes(NONCE_LEN)
        ct = self._aeads[self.current].encrypt(nonce, plaintext, None)
        return nonce + ct, self.current

    def decrypt(self, blob: bytes, version: int) -> bytes:
        aead = self._aeads.get(version)
        if aead is None:
            raise VaultError(f"no key for version {version}")
        return aead.decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], None)


def _decode_key(text: str) -> bytes:
    raw = base64.urlsafe_b64decode(text.strip())
    if len(raw) != KEY_LEN:
        raise VaultError("vault key must decode to 32 bytes")
    return raw


def _file_key(data_dir: Path) -> bytes:
    path = data_dir / KEY_FILE
    if path.exists():
        return _decode_key(path.read_text())
    key = secrets.token_bytes(KEY_LEN)
    path.write_text(base64.urlsafe_b64encode(key).decode())
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return key


def keyring(settings: Settings) -> Keyring:
    keys: dict[int, bytes] = {1: _file_key(settings.data_dir)}
    if settings.vault_key is not None:
        keys[2] = _decode_key(settings.vault_key.get_secret_value())
    return Keyring(keys)


def store(session: Session, household_id: int, ref: str,
          secret: bytes, ring: Keyring) -> VaultEntry:
    if not ref or len(ref) > 128:
        raise VaultError("ref must be 1-128 characters")
    if not secret:
        raise VaultError("secret must be non-empty")
    entry = session.scalar(select(VaultEntry).where(
        VaultEntry.household_id == household_id,
        VaultEntry.ref == ref))
    blob, version = ring.encrypt(secret)
    if entry is None:
        entry = VaultEntry(household_id=household_id, ref=ref,
                           ciphertext=blob, key_version=version)
        session.add(entry)
    else:
        entry.ciphertext = blob
        entry.key_version = version
    session.flush()
    return entry


def retrieve(session: Session, household_id: int, ref: str,
             ring: Keyring) -> bytes:
    """Server-side only — the plaintext must never be attached to
    an API model or response."""
    entry = session.scalar(select(VaultEntry).where(
        VaultEntry.household_id == household_id,
        VaultEntry.ref == ref))
    if entry is None:
        raise VaultError(f"credential {ref!r} not found")
    return ring.decrypt(entry.ciphertext, entry.key_version)


def delete(session: Session, entry: VaultEntry) -> None:
    session.delete(entry)
    session.flush()


def rotate(session: Session, ring: Keyring) -> int:
    """Re-encrypt every row whose ``key_version`` lags the current
    generation. Returns the count re-encrypted."""
    stale = session.scalars(select(VaultEntry).where(
        VaultEntry.key_version != ring.current)).all()
    for e in stale:
        plain = ring.decrypt(e.ciphertext, e.key_version)
        e.ciphertext, e.key_version = ring.encrypt(plain)
    session.flush()
    return len(stale)
