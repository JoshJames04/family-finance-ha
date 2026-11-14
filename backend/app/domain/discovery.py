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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import sync as syncdom
from app.domain.accounts import OPENING_PAYEE
from app.domain.sync import FeedItem
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    ConnStatus,
    MappingStatus,
    SyncCursor,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


class DiscoveryError(ValueError):
    pass


class SpacesUnavailable(DiscoveryError):
    """Optional Spaces permissions do not invalidate the main account."""


@dataclass(frozen=True)
class DiscoveredAccount:
    external_uid: str
    label: str
    kind: str = "account"          # account | space | pot
    balance_pence: int | None = None
    category_uid: str | None = None  # feed category (defaultCategory)


class DiscoveryListing(list[DiscoveredAccount]):
    """List-compatible discovery with explicitly unavailable Spaces parents."""

    def __init__(self, accounts: Iterable[DiscoveredAccount] = ()) -> None:
        super().__init__(accounts)
        self.unavailable_space_accounts: set[str] = set()


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


def unavailable_spaces(client: BankClient) -> set[str]:
    """Optional provider permission gaps for its latest complete account scan."""
    return set(getattr(client, "unavailable_space_accounts", ()))


def defer_unavailable_spaces(session: Session, conn: BankConnection,
                             parents: set[str], interval_seconds: float, *,
                             now: datetime | None = None) -> None:
    """Retry optional permission gaps at normal cadence without claiming success."""
    retry_at = (now or datetime.now()) + timedelta(seconds=interval_seconds)
    for mapping in session.scalars(select(BankAccountMapping).where(
            BankAccountMapping.connection_id == conn.id,
            BankAccountMapping.status == MappingStatus.linked,
            BankAccountMapping.external_kind == "space",
            BankAccountMapping.external_uid.in_(parents),
            BankAccountMapping.account_id.is_not(None))):
        assert mapping.account_id is not None
        cursor = syncdom.cursor_for(session, mapping.account_id, TransactionSource.starling)
        cursor.status = SyncStatus.failed
        cursor.error = "Spaces unavailable — check Spaces read permission"
        cursor.retry_at = max(cursor.retry_at, retry_at) if cursor.retry_at else retry_at
        cursor.refresh_requested = True
    session.flush()


def discover(session: Session, conn: BankConnection,
             client: BankClient, token: str) -> list[BankAccountMapping]:
    """Upsert discovered accounts; return the connection's mapping
    set after reconciliation. Idempotent — same discovery twice is
    a no-op beyond label refresh. Identity is (external_uid,
    category_uid): a space shares its parent account's uid but owns
    its feed category."""
    session.commit()  # release any caller's writer before provider I/O
    listing = client.accounts(token)
    unavailable_spaces = (listing.unavailable_space_accounts
                          if isinstance(listing, DiscoveryListing) else set())
    if unavailable_spaces:
        conn.status = ConnStatus.degraded
        conn.last_error = "Spaces unavailable — check Spaces read permission"
    seen = {(d.external_uid, d.category_uid or ""): d for d in listing}
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
                and m.status != MappingStatus.closed
                and not (m.external_kind == "space"
                         and m.external_uid in unavailable_spaces)):
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
                  client: BankClient, token: str, *,
                  reconcile_opening: bool = False,
                  account_ids: set[int] | None = None,
                  fresh: bool = False) -> int:
    """Refresh provider balances on linked mappings. Point-in-time
    read only — never fabricates ledger transactions."""
    session.commit()  # discovery/caller changes cannot span HTTP
    rows = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == conn.id,
        BankAccountMapping.status == MappingStatus.linked)).all()
    if account_ids is not None:
        rows = [m for m in rows if m.account_id in account_ids]
    if fresh:
        invalidate = getattr(client, "invalidate_balances", None)
        if callable(invalidate):
            invalidate()
    balances = []
    denied_spaces = unavailable_spaces(client)
    for m in rows:
        if m.external_kind == "space" and m.external_uid in denied_spaces:
            continue
        cat = (m.external_category_uid or None
               if m.external_kind == "space" else None)
        try:
            balance = client.balance(token, m.external_uid, cat)
        except SpacesUnavailable:
            denied_spaces.add(m.external_uid)
            conn.status = ConnStatus.degraded
            conn.last_error = "Spaces unavailable — check Spaces read permission"
            continue
        balances.append((m, balance))
    for m, balance in balances:
        m.balance_pence = balance
        m.balance_at = datetime.now()
        if reconcile_opening and m.account_id is not None:
            _reconcile_opening(session, m.account_id, m.balance_pence)
    session.flush()
    return len(balances)


def reconcile_synced_balances(session: Session, conn: BankConnection,
                              client: BankClient, token: str,
                              account_ids: set[int], *,
                              interval_seconds: float = 240 * 60) -> int:
    """Refresh only completed feeds requiring the existing pre-opening repair.

    Ordinary incremental sync needs no extra balance request or opening rewrite.
    A discovery-time Spaces cache is too old to repair newly ingested history.
    """
    repair_ids = set(session.scalars(select(Account.id).join(
        Transaction, Transaction.account_id == Account.id).join(
        SyncCursor, SyncCursor.account_id == Account.id).where(
            Account.id.in_(account_ids), Transaction.date < Account.opening_date,
            Transaction.status != TransactionStatus.void,
            SyncCursor.source == TransactionSource.starling,
            SyncCursor.status == SyncStatus.completed).distinct()))
    if not repair_ids:
        return 0
    count = sync_balances(session, conn, client, token, reconcile_opening=True,
                          account_ids=repair_ids, fresh=True)
    denied = unavailable_spaces(client)
    if denied:
        defer_unavailable_spaces(session, conn, denied, interval_seconds)
    return count


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
