"""Account discovery + balance sync (issue #66).

A provider client (injected — stdlib Starling client wires at the
API layer) reports external accounts/spaces; discovery upserts
``BankAccountMapping`` proposals keyed by (connection, external_uid)
so re-discovery is idempotent: renames update the label, accounts
that stop appearing mark closed, previously ignored/linked mappings
are never re-proposed. Balance sync is a point-in-time read onto the
mapping — it never writes to the ledger (the internal ledger
remains the source of truth for reporting).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    MappingStatus,
)


class DiscoveryError(ValueError):
    pass


@dataclass(frozen=True)
class DiscoveredAccount:
    external_uid: str
    label: str
    kind: str = "account"          # account | space | pot
    balance_pence: int | None = None


class BankClient(Protocol):
    """Provider-facing surface needed by discovery — the Starling
    adapter implements accounts() via GET /api/v2/accounts +
    spaces; balance() via /accounts/{uid}/balance."""

    def accounts(self, token: str) -> list[DiscoveredAccount]: ...
    def balance(self, token: str,
                external_uid: str) -> int: ...


def discover(session: Session, conn: BankConnection,
             client: BankClient, token: str) -> list[BankAccountMapping]:
    """Upsert discovered accounts; return the connection's mapping
    set after reconciliation. Idempotent — same discovery twice is
    a no-op beyond label refresh."""
    seen = {d.external_uid: d for d in client.accounts(token)}
    rows = list(session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == conn.id)))
    by_uid = {m.external_uid: m for m in rows}
    for uid, d in seen.items():
        m = by_uid.get(uid)
        if m is None:
            m = BankAccountMapping(
                household_id=conn.household_id,
                connection_id=conn.id, external_uid=uid,
                external_label=d.label, external_kind=d.kind,
                status=MappingStatus.proposed)
            session.add(m)
            rows.append(m)
        else:
            m.external_label = d.label          # rename propagates
            m.external_kind = d.kind
            if m.status == MappingStatus.closed:
                m.status = (MappingStatus.linked
                            if m.account_id is not None
                            else MappingStatus.proposed)
        if d.balance_pence is not None:
            m.balance_pence = d.balance_pence
            m.balance_at = datetime.now()
    for m in rows:
        if (m.external_uid not in seen
                and m.status != MappingStatus.closed):
            m.status = MappingStatus.closed     # gone provider-side
    session.flush()
    return rows


def link(session: Session, mapping: BankAccountMapping,
         account: Account) -> None:
    """User confirms a mapping to an existing internal account."""
    if account.household_id != mapping.household_id:
        raise DiscoveryError("account belongs to another household")
    mapping.account_id = account.id
    mapping.status = MappingStatus.linked
    session.flush()


def ignore(session: Session, mapping: BankAccountMapping) -> None:
    if mapping.status != MappingStatus.proposed:
        raise DiscoveryError("only proposed mappings can be ignored")
    mapping.status = MappingStatus.ignored
    session.flush()


def sync_balances(session: Session, conn: BankConnection,
                  client: BankClient, token: str) -> int:
    """Refresh provider balances on linked mappings. Point-in-time
    read only — never fabricates ledger transactions."""
    rows = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == conn.id,
        BankAccountMapping.status == MappingStatus.linked)).all()
    for m in rows:
        m.balance_pence = client.balance(token, m.external_uid)
        m.balance_at = datetime.now()
    session.flush()
    return len(rows)
