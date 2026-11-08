"""Issue #66 — account discovery + balance sync.

ACs: re-discovery is idempotent (link once; renames propagate;
missing accounts close); balance updates never fabricate
transactions.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import banks, discovery, vault
from app.domain.banks import TokenInfo
from app.domain.discovery import DiscoveredAccount
from app.persistence.models import (
    Account,
    BankConnection,
    BankProvider,
    Household,
    MappingStatus,
    Transaction,
    TransactionSource,
)


class FakeClient:
    def __init__(self, items: list[DiscoveredAccount],
                 balances: dict[str, int] | None = None) -> None:
        self.items = items
        self.balances = balances or {}
        self.calls = 0

    def accounts(self, token: str) -> list[DiscoveredAccount]:
        return self.items

    def balance(self, token: str, external_uid: str,
                category_uid: str | None = None) -> int:
        self.calls += 1
        return self.balances[category_uid or external_uid]


def _validator(t: str) -> TokenInfo:
    return TokenInfo(scopes=frozenset(
        {"account:read", "balance:read", "transaction:read"}))


@pytest.fixture()
def conn(session: Session, household: Household,
         tmp_path: Path) -> BankConnection:
    ring = vault.keyring(Settings.load(
        {"FF_DATA_DIR": str(tmp_path)}))
    return banks.connect(session, household.id,
                         BankProvider.starling, "pat-1",
                         _validator, ring)


@pytest.fixture()
def ring(tmp_path: Path) -> vault.Keyring:
    return vault.keyring(Settings.load(
        {"FF_DATA_DIR": str(tmp_path)}))


def _account(session: Session, household: Household,
             name: str = "Current") -> Account:
    a = Account(household_id=household.id, name=name,
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


class TestDiscovery:
    def test_first_discovery_proposes(
            self, session: Session, conn: BankConnection) -> None:
        client = FakeClient([
            DiscoveredAccount("uid-1", "Personal"),
            DiscoveredAccount("uid-2", "Holiday", "space")])
        rows = discovery.discover(session, conn, client, "pat-1")
        assert len(rows) == 2
        assert all(m.status == MappingStatus.proposed
                   for m in rows)

    def test_spaces_share_account_uid_without_colliding(
            self, session: Session, conn: BankConnection) -> None:
        """A space and its parent account share external_uid — the
        (uid, category) key keeps them as separate mappings."""
        client = FakeClient([
            DiscoveredAccount("uid-1", "Personal", "account",
                              category_uid="cat-main"),
            DiscoveredAccount("uid-1", "Joint Bills", "space",
                              category_uid="cat-space")])
        rows = discovery.discover(session, conn, client, "pat-1")
        assert len(rows) == 2
        cats = {m.external_category_uid for m in rows}
        assert cats == {"cat-main", "cat-space"}
        # Re-discover closing just the space leaves the account open.
        client.items = [
            DiscoveredAccount("uid-1", "Personal", "account",
                              category_uid="cat-main")]
        rows = discovery.discover(session, conn, client, "pat-1")
        by_cat = {m.external_category_uid: m for m in rows}
        assert by_cat["cat-space"].status == MappingStatus.closed
        assert by_cat["cat-main"].status == MappingStatus.proposed

    def test_rediscovery_is_idempotent(
            self, session: Session, household: Household,
            conn: BankConnection) -> None:
        """AC: re-discovery never re-proposes — linked stays
        linked; renames update; vanished uids close."""
        acct = _account(session, household)
        client = FakeClient([DiscoveredAccount("uid-1", "Personal")])
        rows = discovery.discover(session, conn, client, "pat-1")
        discovery.link(session, rows[0], acct)
        # Re-discover with a rename + a new account, uid-1 retained.
        client.items = [DiscoveredAccount("uid-1", "Personal Renamed"),
                        DiscoveredAccount("uid-3", "New Space",
                                          "space")]
        rows = discovery.discover(session, conn, client, "pat-1")
        by_uid = {m.external_uid: m for m in rows}
        assert len(rows) == 2                       # no duplicates
        assert by_uid["uid-1"].status == MappingStatus.linked
        assert by_uid["uid-1"].external_label == "Personal Renamed"
        assert by_uid["uid-3"].status == MappingStatus.proposed
        # Drop uid-1 provider-side → closes, doesn't re-propose.
        client.items = [DiscoveredAccount("uid-3", "New Space",
                                          "space")]
        rows = discovery.discover(session, conn, client, "pat-1")
        by_uid = {m.external_uid: m for m in rows}
        assert by_uid["uid-1"].status == MappingStatus.closed
        assert len(rows) == 2

    def test_ignored_never_reproposes(
            self, session: Session, conn: BankConnection) -> None:
        client = FakeClient([DiscoveredAccount("uid-1", "Pot")])
        rows = discovery.discover(session, conn, client, "pat-1")
        discovery.ignore(session, rows[0])
        rows = discovery.discover(session, conn, client, "pat-1")
        assert rows[0].status == MappingStatus.ignored


class TestLinking:
    def test_cross_household_link_rejected(
            self, session: Session, household: Household,
            conn: BankConnection) -> None:
        other_hh = Household(name="Other")
        session.add(other_hh)
        session.flush()
        foreign = _account(session, other_hh, "Foreign")
        rows = discovery.discover(
            session, conn,
            FakeClient([DiscoveredAccount("uid-1", "A")]), "pat-1")
        with pytest.raises(discovery.DiscoveryError):
            discovery.link(session, rows[0], foreign)


class TestBalances:
    def test_balance_sync_never_writes_ledger(
            self, session: Session, household: Household,
            conn: BankConnection) -> None:
        """AC: balance updates are point-in-time reads — zero
        ledger writes."""
        acct = _account(session, household)
        client = FakeClient(
            [DiscoveredAccount("uid-1", "Personal")],
            balances={"uid-1": 152_340})
        rows = discovery.discover(session, conn, client, "pat-1")
        discovery.link(session, rows[0], acct)
        n = discovery.sync_balances(session, conn, client, "pat-1")
        assert n == 1
        m = rows[0]
        assert m.balance_pence == 152_340
        assert m.balance_at is not None
        count = session.scalar(select(func.count(Transaction.id))
                               .where(Transaction.account_id
                                      == acct.id))
        assert (count or 0) == 0    # nothing fabricated

    def test_unlinked_mappings_not_polled(
            self, session: Session, conn: BankConnection) -> None:
        client = FakeClient(
            [DiscoveredAccount("uid-1", "Personal")])
        discovery.discover(session, conn, client, "pat-1")
        n = discovery.sync_balances(session, conn, client, "pat-1")
        assert n == 0
        assert client.calls == 0    # proposed rows aren't polled


class TestFeedMapping:
    def test_feed_items_map_direction_and_status(self) -> None:
        """Spike #64 contract: unsigned amount + direction ->
        signed pence; feed status -> TransactionStatus."""
        from app.integrations.starling import StarlingClient
        from app.persistence.models import TransactionStatus
        raw = [
            {"feedItemUid": "u1",
             "amount": {"currency": "GBP", "minorUnits": 1250},
             "direction": "IN", "status": "SETTLED",
             "counterPartyName": "Employer Ltd",
             "transactionTime": "2025-05-02T09:00:00.000Z"},
            {"feedItemUid": "u2",
             "amount": {"currency": "GBP", "minorUnits": 500},
             "direction": "OUT", "status": "PENDING",
             "counterPartyName": "Shop",
             "transactionTime": "2025-05-03T12:00:00.000Z"}]
        client = StarlingClient()
        client_items = [client._map(r) for r in raw]
        assert client_items[0] is not None
        assert client_items[1] is not None
        assert client_items[0].amount_pence == 1250
        assert client_items[1].amount_pence == -500
        assert client_items[0].status == TransactionStatus.cleared
        assert client_items[1].status == TransactionStatus.pending
        assert client_items[0].payee == "Employer Ltd"
        assert client_items[0].external_ref == "u1"

    def test_pending_to_settled_updates_in_place(
            self, session: Session, household: Household) -> None:
        """AC: a re-fetched ref (PENDING -> SETTLED) updates the
        mutable fields — dedup-or-update, not pure skip."""
        from app.domain import sync as sd
        from app.persistence.models import TransactionStatus as TS
        a = Account(household_id=household.id, name="C",
                    type="personal", opening_balance_pence=0,
                    opening_date=date(2025, 1, 1))
        session.add(a)
        session.flush()
        src = TransactionSource.starling
        pend = [sd.FeedItem("u1", date(2025, 5, 2), -500, "Shop",
                            status=TS.pending)]
        sd.sync_account(session, a, src,
                        lambda c: iter([sd.FeedPage(pend)]))
        t = session.scalar(select(Transaction))
        assert t is not None
        assert t.status == TS.pending
        settled = [sd.FeedItem("u1", date(2025, 5, 2), -500,
                               "Shop", status=TS.cleared)]
        run = sd.sync_account(
            session, a, src,
            lambda c: iter([sd.FeedPage(settled)]))
        assert run.inserted == 0
        assert run.deduped == 1
        session.refresh(t)
        assert t.status == TS.cleared   # updated, not duplicated
