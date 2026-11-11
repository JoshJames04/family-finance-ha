"""Issue #67 — incremental feed sync.

ACs: re-syncing an identical window creates zero duplicates; overlap
with imported/manual history yields merge candidates, not
double-counted spend; sync survives mid-run failure and resumes.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import sync as sync_domain
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    MergeCandidate,
    MergeStatus,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

SRC = TransactionSource.starling


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _feed(*items: sync_domain.FeedItem):
    """One page per call — the simplest possible feed."""
    yield sync_domain.FeedPage(list(items))


def _item(ref: str | None, pence: int, payee: str = "Shop",
          on: date = date(2025, 4, 10)) -> sync_domain.FeedItem:
    return sync_domain.FeedItem(ref, on, pence, payee)


def _count(session: Session, a: Account) -> int:
    return int(session.scalar(
        select(func.count(Transaction.id))
        .where(Transaction.account_id == a.id,
               Transaction.status != TransactionStatus.void)) or 0)


class TestExactDedup:
    def test_resync_identical_window_zero_dupes(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: re-syncing an identical window creates zero
        duplicates."""
        feed = [_item("r1", -1_000), _item("r2", -2_000),
                _item("r3", 5_000)]
        r1 = sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(*feed))
        assert r1.inserted == 3
        assert _count(session, account) == 3
        r2 = sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(*feed))
        assert r2.inserted == 0
        assert r2.deduped == 3
        assert _count(session, account) == 3

    def test_dedup_backfills_provider_category(
            self, session: Session, household: Household,
            account: Account) -> None:
        """Issue #453 — rows ingested before provider categories were
        captured backfill on feed redelivery; a later enum change is
        provider-owned and refreshes too."""
        bare = sync_domain.FeedItem("r1", date(2025, 4, 10), -1_000, "Shop")
        sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(bare))
        txn = session.scalar(select(Transaction))
        assert txn is not None and txn.provider_category is None

        tagged = sync_domain.FeedItem(
            "r1", date(2025, 4, 10), -1_000, "Shop",
            provider_category="GROCERIES")
        r2 = sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(tagged))
        assert r2.deduped == 1
        session.refresh(txn)
        assert txn.provider_category == "GROCERIES"

        retagged = sync_domain.FeedItem(
            "r1", date(2025, 4, 10), -1_000, "Shop",
            provider_category="EATING_OUT")
        sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(retagged))
        session.refresh(txn)
        assert txn.provider_category == "EATING_OUT"


class TestFuzzyOverlap:
    def test_imported_history_becomes_candidate(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: overlap with imported aggregates produces merge
        candidates, not double-counted spend."""
        session.add(Transaction(
            account_id=account.id, date=date(2025, 4, 10),
            amount_pence=-2_500, payee="Tesco",
            status=TransactionStatus.cleared,
            source=TransactionSource.excel_import))
        session.flush()
        feed = [_item("bank-9", -2_500, "Tesco"),
                _item("bank-10", -1_000, "NewShop")]
        run = sync_domain.sync_account(
            session, account, SRC, lambda c: _feed(*feed))
        assert run.inserted == 1          # only the genuinely new row
        assert run.merge_candidates == 1
        assert _count(session, account) == 2   # not 3 — no double-count
        c = session.scalar(select(MergeCandidate))
        assert c is not None
        assert c.status == MergeStatus.pending
        assert c.incoming["external_ref"] == "bank-9"

    def test_resolve_merged_annotates_ref(
            self, session: Session, household: Household,
            account: Account) -> None:
        orig = Transaction(
            account_id=account.id, date=date(2025, 4, 10),
            amount_pence=-2_500, payee="Tesco",
            status=TransactionStatus.cleared,
            source=TransactionSource.excel_import)
        session.add(orig)
        session.flush()
        sync_domain.sync_account(
            session, account, SRC,
            lambda c: _feed(_item("bank-9", -2_500, "Tesco")))
        c = session.scalar(select(MergeCandidate))
        assert c is not None
        sync_domain.resolve_merge(session, c, "merged")
        assert orig.external_ref == "bank-9"
        assert c.status == MergeStatus.merged
        # A later feed carrying the same ref now dedups exactly.
        run = sync_domain.sync_account(
            session, account, SRC,
            lambda c: _feed(_item("bank-9", -2_500, "Tesco")))
        assert run.inserted == 0
        assert _count(session, account) == 1


class TestResume:
    def test_mid_run_failure_resumes(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: sync survives mid-run failure and resumes from the
        last committed page."""
        pages = [[_item("r1", -1_000), _item("r2", -1_100)],
                 [_item("r3", -1_200)],
                 [_item("r4", -1_300)]]

        def flaky(cursor):
            yield sync_domain.FeedPage(pages[0])
            raise sync_domain.RateLimited("429")

        run = sync_domain.sync_account(
            session, account, SRC, flaky)
        assert run.status == SyncStatus.failed
        assert run.inserted == 2
        cursor = sync_domain.cursor_for(session, account.id, SRC)
        assert cursor.status == SyncStatus.failed
        assert cursor.last_ref == "r2"

        def rest(c):
            for p in pages[1:]:
                yield sync_domain.FeedPage(p)

        run2 = sync_domain.sync_account(
            session, account, SRC, rest)
        assert run2.status == SyncStatus.completed
        assert run2.inserted == 2
        assert _count(session, account) == 4
        assert cursor.status == SyncStatus.completed
        assert cursor.last_ref == "r4"

    def test_rate_limit_is_resumable_not_fatal(
            self, session: Session, household: Household,
            account: Account) -> None:
        def limited(c):
            raise sync_domain.RateLimited("429 too many")
            yield
        run = sync_domain.sync_account(
            session, account, SRC, limited)
        assert run.status == SyncStatus.failed
        assert "429" in (run.error or "")


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_cursor_and_candidates_empty(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        r = client.get(f"/api/v1/sync/accounts/{aid}/cursor",
                       headers=h)
        assert r.status_code == 200
        assert r.json() == []
        r = client.get("/api/v1/sync/merge-candidates", headers=h)
        assert r.status_code == 200
        assert r.json() == []
