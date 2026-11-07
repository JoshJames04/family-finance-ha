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
)


class FakeClient:
    def __init__(self, items: list[DiscoveredAccount],
                 balances: dict[str, int] | None = None) -> None:
        self.items = items
        self.balances = balances or {}
        self.calls = 0

    def accounts(self, token: str) -> list[DiscoveredAccount]:
        return self.items

    def balance(self, token: str, external_uid: str) -> int:
        self.calls += 1
        return self.balances[external_uid]


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
