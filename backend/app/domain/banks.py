"""Bank connection lifecycle (issue #65).

State machine over a vault-backed token. Token validation is an
injected callable — in production it calls Starling's
``GET /api/v2/identity/token`` (returns the token's scopes); tests
inject fakes covering connected / 401 / 403 / scope-violating /
rate-limited paths.

Least-privilege is enforced here: any token holding a scope outside
READ_SCOPES is rejected outright — a PAT can carry payment powers
and ADR-0008 forbids them ever reaching the vault.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from cryptography.exceptions import InvalidTag
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import appconfig, vault
from app.domain import sync as syncdom
from app.domain.vault import Keyring, VaultError
from app.persistence.models import (
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    MappingStatus,
    SyncCursor,
    SyncStatus,
    TransactionSource,
    VaultEntry,
)

REQUIRED_SCOPES = frozenset({
    "account:read", "balance:read", "transaction:read"})
RATE_LIMIT_ERROR = "Provider rate limited"
RETRY_KEY_PREFIX = "bank_retry_at:"


class ConnError(ValueError):
    pass


class TokenInvalid(ConnError):
    """401/403 — token dead; connection revokes."""


class TokenRejected(ConnError):
    """Token holds a non-read scope, or is missing a required one."""


class ValidatorUnavailable(ConnError):
    """429/5xx/network — transient; connection degrades."""


@dataclass(frozen=True)
class TokenInfo:
    scopes: frozenset[str]
    label: str | None = None


Validator = Callable[[str], TokenInfo]


def vault_ref(provider: BankProvider, conn_id: int) -> str:
    return f"{provider.value}:conn:{conn_id}"


def _check_scopes(scopes: frozenset[str]) -> None:
    # Any `*:read` scope is harmless — Starling adds read scopes over
    # time and PAT creators tick extra boxes; only write powers
    # (`:create`, `:edit`, `:delete`) violate ADR-0008's read-only rule.
    extra = {s for s in scopes if not s.endswith(":read")}
    if extra:
        raise TokenRejected(
            "token holds non-read scopes: " + ", ".join(sorted(extra)))
    missing = REQUIRED_SCOPES - scopes
    if missing:
        raise TokenRejected(
            "token lacks required scopes: " + ", ".join(
                sorted(missing)))


def connect(session: Session, household_id: int,
            provider: BankProvider, token: str,
            validate: Validator, ring: Keyring,
            existing: BankConnection | None = None,
            label: str | None = None) -> BankConnection:
    """Validate → vault-store → mark connected. ``existing`` is the
    revoked/degraded row being re-authenticated (rotation keeps the
    connection id stable); ``label`` names the link ("Joint")."""
    try:
        info = validate(token)
    except TokenInvalid:
        raise
    except ConnError:
        raise
    except Exception as e:
        raise ValidatorUnavailable(str(e)) from e
    _check_scopes(info.scopes)
    conn = existing or BankConnection(
        household_id=household_id, provider=provider,
        vault_ref="", status=ConnStatus.pending)
    if existing is None:
        session.add(conn)
        session.flush()            # mint id for the vault ref
        conn.vault_ref = vault_ref(provider, conn.id)
    vault.store(session, household_id, conn.vault_ref,
                token.encode(), ring)
    conn.status = ConnStatus.connected
    conn.scopes = " ".join(sorted(info.scopes))
    conn.label = label or info.label or conn.label
    conn.last_validated_at = datetime.now()
    conn.last_error = None
    appconfig.clear_value(session, f"{RETRY_KEY_PREFIX}{conn.id}")
    session.flush()
    return conn


def record_api_error(session: Session, conn: BankConnection,
                     status_code: int | None,
                     message: str = "",
                     retry_after: float | None = None) -> ConnStatus:
    """Map an upstream failure onto the state machine. 401/403 →
    revoked (token dead — sync stops cleanly, re-auth offered);
    anything else → degraded (transient — sync may retry)."""
    previous_retry = connection_retry_at(session, conn) if status_code == 429 else None
    conn.last_error = message[:500] or f"HTTP {status_code}"
    conn.status = (ConnStatus.revoked
                   if status_code in (401, 403)
                   else ConnStatus.degraded)
    if status_code == 429:
        delay = (retry_after if retry_after is not None and isfinite(retry_after)
                 and retry_after > 0 else 60)
        retry_at = datetime.now() + timedelta(seconds=delay)
        if previous_retry is not None:
            retry_at = max(previous_retry, retry_at)
        conn.last_error = f"{RATE_LIMIT_ERROR} until {retry_at.isoformat()}: {message}"[:500]
        appconfig.set_value(session, f"{RETRY_KEY_PREFIX}{conn.id}", retry_at.isoformat())
        for mapping in session.scalars(select(BankAccountMapping).where(
                BankAccountMapping.connection_id == conn.id,
                BankAccountMapping.status == MappingStatus.linked,
                BankAccountMapping.account_id.is_not(None))):
            assert mapping.account_id is not None
            cursor = syncdom.cursor_for(session, mapping.account_id, TransactionSource.starling)
            cursor.status = SyncStatus.failed
            cursor.error = conn.last_error
            cursor.retry_at = retry_at
            cursor.refresh_requested = True
    session.flush()
    return conn.status


def connection_retry_at(session: Session, conn: BankConnection,
                        now: datetime | None = None) -> datetime | None:
    """Only provider429 embargoes all feeds; optional permission delays do not."""
    now = now or datetime.now()
    candidates = []
    raw = appconfig.get_value(session, f"{RETRY_KEY_PREFIX}{conn.id}")
    if raw:
        try:
            stored = datetime.fromisoformat(raw)
        except ValueError:
            pass
        else:
            if stored.tzinfo is None and stored > now:
                candidates.append(stored)
    if conn.last_error and conn.last_error.startswith(RATE_LIMIT_ERROR):
        legacy = session.scalar(select(func.max(SyncCursor.retry_at)).join(
            BankAccountMapping, BankAccountMapping.account_id == SyncCursor.account_id).where(
                BankAccountMapping.connection_id == conn.id,
                BankAccountMapping.status == MappingStatus.linked,
                SyncCursor.source == TransactionSource.starling,
                SyncCursor.retry_at > now))
        if legacy is not None:
            candidates.append(legacy)
    return max(candidates, default=None)


def rotate(session: Session, conn: BankConnection, token: str,
           validate: Validator, ring: Keyring) -> BankConnection:
    """Re-authenticate: works from revoked, degraded or connected
    (proactive rotation)."""
    return connect(session, conn.household_id, conn.provider,
                   token, validate, ring, existing=conn)


def disconnect(session: Session, conn: BankConnection,
               ring: Keyring) -> None:
    """User-initiated removal — purges the vault ref so no token
    material survives, then revokes the connection row (audit
    trail) rather than deleting it."""
    entry = session.scalar(select(VaultEntry).where(
        VaultEntry.household_id == conn.household_id,
        VaultEntry.ref == conn.vault_ref))
    if entry is not None:
        vault.delete(session, entry)
    conn.status = ConnStatus.revoked
    conn.last_error = "disconnected by user"
    session.flush()


def token(session: Session, conn: BankConnection,
          ring: Keyring) -> str:
    """Server-side token access for the API client — never crosses
    an API boundary."""
    if conn.status == ConnStatus.revoked:
        raise ConnError("connection is revoked — re-authenticate")
    try:
        return vault.retrieve(
            session, conn.household_id, conn.vault_ref, ring).decode()
    except (VaultError, InvalidTag) as e:
        # issue #349: ciphertext written under another install's
        # .vault_key (or a missing ref) is unrecoverable locally —
        # the user must re-enter the token.
        conn.last_error = "stored credential unreadable — re-authenticate"
        raise ConnError(conn.last_error) from e


def syncable(conn: BankConnection) -> bool:
    return conn.status in (ConnStatus.connected,
                           ConnStatus.degraded)
