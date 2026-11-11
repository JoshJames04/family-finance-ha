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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.accounts import OPENING_PAYEE
from app.domain.sync import FeedItem
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    MappingStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


class DiscoveryError(ValueError):
    pass


@dataclass(frozen=True)
class DiscoveredAccount:
    external_uid: str
    label: str
    kind: str = "account"          # account | space | pot
    balance_pence: int | None = None
    category_uid: str | None = None  # feed category (defaultCategory)


class BankClient(Protocol):
    """Provider-facing surface needed by discovery — the Starling
    adapter implements accounts() via GET /api/v2/accounts +
    spaces; balance() via /accounts/{uid}/balance."""

    def accounts(self, token: str) -> list[DiscoveredAccount]: ...
    def balance(self, token: str, external_uid: str,
                category_uid: str | None = None) -> int: ...
    def feed_items(self, token: str, external_uid: str,
                   category_uid: str, since: str
                   ) -> list[FeedItem]: ...


def discover(session: Session, conn: BankConnection,
             client: BankClient, token: str) -> list[BankAccountMapping]:
    """Upsert discovered accounts; return the connection's mapping
    set after reconciliation. Idempotent — same discovery twice is
    a no-op beyond label refresh. Identity is (external_uid,
    category_uid): a space shares its parent account's uid but owns
    its feed category."""
    session.commit()  # release any caller's writer before provider I/O
    seen = {(d.external_uid, d.category_uid or ""): d
            for d in client.accounts(token)}
    rows = list(session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == conn.id)))
    by_uid = {(m.external_uid, m.external_category_uid): m
              for m in rows}
    for (uid, cat), d in seen.items():
        m = by_uid.get((uid, cat))
        if m is None:
            m = BankAccountMapping(
                household_id=conn.household_id,
                connection_id=conn.id, external_uid=uid,
                external_label=d.label, external_kind=d.kind,
                external_category_uid=d.category_uid or "",
                status=MappingStatus.proposed)
            session.add(m)
            rows.append(m)
        else:
            old_label = m.external_label
            m.external_label = d.label          # rename propagates
            m.external_kind = d.kind
            m.external_category_uid = d.category_uid or ""
            if m.account_id is not None and d.label != old_label:
                acct = session.get(Account, m.account_id)
                # Only rename the internal account when it still
                # carries the provider's previous label — a
                # user-customised name is never stomped.
                if acct is not None and acct.name == old_label:
                    acct.name = d.label
            if m.status == MappingStatus.closed:
                m.status = (MappingStatus.linked
                            if m.account_id is not None
                            else MappingStatus.proposed)
        if d.balance_pence is not None:
            m.balance_pence = d.balance_pence
            m.balance_at = datetime.now()
    for m in rows:
        if ((m.external_uid, m.external_category_uid) not in seen
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


def unignore(session: Session, mapping: BankAccountMapping) -> None:
    if mapping.status != MappingStatus.ignored:
        raise DiscoveryError("only ignored mappings can be un-ignored")
    mapping.status = MappingStatus.proposed
    session.flush()


def sync_balances(session: Session, conn: BankConnection,
                  client: BankClient, token: str) -> int:
    """Refresh provider balances on linked mappings. Point-in-time
    read only — never fabricates ledger transactions."""
    session.commit()  # discovery/caller changes cannot span HTTP
    rows = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == conn.id,
        BankAccountMapping.status == MappingStatus.linked)).all()
    balances = []
    for m in rows:
        cat = (m.external_category_uid or None
               if m.external_kind == "space" else None)
        balances.append((m, client.balance(token, m.external_uid, cat)))
    for m, balance in balances:
        m.balance_pence = balance
        m.balance_at = datetime.now()
        if m.account_id is not None:
            _reconcile_opening(session, m.account_id, m.balance_pence)
    session.flush()
    return len(rows)


def _reconcile_opening(session: Session, account_id: int,
                       balance_pence: int) -> None:
    """Issue #348: an opening stamped at *link day* while the feed
    already holds earlier history double-counts that history in the
    ledger. Restamp (or create) the opening so the ledger agrees
    with the provider — but only once ledger history predating
    ``opening_date`` actually exists, so a fresh link before the
    first feed sync never fabricates a phantom opening."""
    open_ref = f"open:{account_id}"
    acct = session.get(Account, account_id)
    if acct is None:
        return
    earliest = session.scalar(select(func.min(Transaction.date)).where(
        Transaction.account_id == account_id,
        Transaction.status != TransactionStatus.void,
        func.coalesce(Transaction.external_ref, "") != open_ref))
    if earliest is None or earliest >= acct.opening_date:
        return
    opening = session.scalar(select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.external_ref == open_ref))
    others = int(session.scalar(select(func.coalesce(
        func.sum(Transaction.amount_pence), 0)).where(
        Transaction.account_id == account_id,
        Transaction.status != TransactionStatus.void,
        func.coalesce(Transaction.external_ref, "") != open_ref)) or 0)
    desired = balance_pence - others
    if opening is not None:
        if opening.amount_pence != desired:
            opening.amount_pence = desired
            acct.opening_balance_pence = desired
    else:
        session.add(Transaction(
            account_id=account_id, date=acct.opening_date,
            amount_pence=desired, payee=OPENING_PAYEE,
            status=TransactionStatus.reconciled,
            source=TransactionSource.system, external_ref=open_ref))
        acct.opening_balance_pence = desired
