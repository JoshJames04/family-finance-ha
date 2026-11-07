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
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import vault
from app.domain.vault import Keyring
from app.persistence.models import (
    BankConnection,
    BankProvider,
    ConnStatus,
    VaultEntry,
)

READ_SCOPES = frozenset({
    "account:read", "account-holder:read", "account-holder-name:read",
    "account-holder-type:read", "account-identifier:read",
    "account-list:read", "balance:read", "transaction:read",
    "savings-goal:read", "space:read", "customer:read",
})
REQUIRED_SCOPES = frozenset({
    "account:read", "balance:read", "transaction:read"})


class ConnError(ValueError):
    pass


class TokenInvalid(ConnError):
    """401/403 — token dead; connection revokes."""


class TokenRejected(ConnError):
    """Scope set outside the read-only allowlist or missing a
    required scope."""


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
    extra = scopes - READ_SCOPES
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
            existing: BankConnection | None = None
            ) -> BankConnection:
    """Validate → vault-store → mark connected. ``existing`` is the
    revoked/degraded row being re-authenticated (rotation keeps the
    connection id stable)."""
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
    conn.label = info.label or conn.label
    conn.last_validated_at = datetime.now()
    conn.last_error = None
    session.flush()
    return conn


def record_api_error(session: Session, conn: BankConnection,
                     status_code: int | None,
                     message: str = "") -> ConnStatus:
    """Map an upstream failure onto the state machine. 401/403 →
    revoked (token dead — sync stops cleanly, re-auth offered);
    anything else → degraded (transient — sync may retry)."""
    conn.last_error = message[:500] or f"HTTP {status_code}"
    conn.status = (ConnStatus.revoked
                   if status_code in (401, 403)
                   else ConnStatus.degraded)
    session.flush()
    return conn.status


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
    return vault.retrieve(
        session, conn.household_id, conn.vault_ref, ring).decode()


def syncable(conn: BankConnection) -> bool:
    return conn.status in (ConnStatus.connected,
                           ConnStatus.degraded)
