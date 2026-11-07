"""Issue #89 — per-member contribution tracking.

ACs: period totals equal matched transfers (no phantom
contributions); private transfers never appear in shared views.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import contributions
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Role,
    Transaction,
    TransactionStatus,
    Transfer,
    UserProfile,
)

START, END = date(2025, 4, 1), date(2025, 4, 30)


def _member(session: Session, household: Household,
            name: str) -> UserProfile:
    m = UserProfile(household_id=household.id, display_name=name,
                    role=Role.member)
    session.add(m)
    session.flush()
    return m


def _account(session: Session, household: Household, name: str,
             owner: UserProfile | None) -> Account:
    a = Account(household_id=household.id, name=name, type="personal",
                opening_balance_pence=0,
                opening_date=date(2025, 1, 1),
                owner_profile_id=owner.id if owner else None)
    session.add(a)
    session.flush()
    return a


def _matched_transfer(session: Session, household: Household,
                      src: Account, dst: Account, pence: int,
                      on: date) -> ExpectedMatch:
    """Transfer + an expected in-leg on dst + the match linking them."""
    t = Transfer(household_id=household.id)
    session.add(t)
    session.flush()
    session.add_all([
        Transaction(account_id=src.id, date=on, amount_pence=-pence,
                    payee="move", transfer_id=t.id,
                    status=TransactionStatus.cleared, source="manual"),
        Transaction(account_id=dst.id, date=on, amount_pence=pence,
                    payee="move", transfer_id=t.id,
                    status=TransactionStatus.cleared, source="manual"),
    ])
    session.flush()
    in_leg = session.query(Transaction).filter(
        Transaction.transfer_id == t.id,
        Transaction.amount_pence > 0).one()
    mv = ExpectedMovement(
        household_id=household.id, account_id=dst.id, expected_date=on,
        amount_pence=pence, label="share",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.transfer_rule, source_id=1)
    session.add(mv)
    session.flush()
    m = ExpectedMatch(movement_id=mv.id, transaction_id=in_leg.id,
                      matched_pence=pence)
    session.add(m)
    session.flush()
    return m


class TestMemberTotals:
    def test_totals_equal_matched_transfers(
            self, session: Session, household: Household) -> None:
        """AC: Σ member contributions == Σ matched transfer pence —
        no phantom contributions from scheduled amounts."""
        ada = _member(session, household, "Ada")
        bob = _member(session, household, "Bob")
        pa = _account(session, household, "Ada acct", ada)
        pb = _account(session, household, "Bob acct", bob)
        joint = _account(session, household, "Joint", None)

        _matched_transfer(session, household, pa, joint, 30_000,
                          date(2025, 4, 5))
        _matched_transfer(session, household, pb, joint, 20_000,
                          date(2025, 4, 6))

        out = {c.display_name: c.contributed_pence
               for c in contributions.member_contributions(
                   session, household.id, start=START, end=END)}
        assert out == {"Ada": 30_000, "Bob": 20_000}
        assert sum(out.values()) == 50_000

    def test_private_transfer_excluded(
            self, session: Session, household: Household) -> None:
        """AC: same-owner→same-owner transfers are private and never
        appear in the shared view."""
        ada = _member(session, household, "Ada")
        a1 = _account(session, household, "Ada current", ada)
        a2 = _account(session, household, "Ada savings", ada)
        joint = _account(session, household, "Joint", None)

        # private shuffle between Ada's own accounts — matched but
        # must not count as a household contribution
        _matched_transfer(session, household, a1, a2, 15_000,
                          date(2025, 4, 5))
        # real contribution Ada → shared joint
        _matched_transfer(session, household, a1, joint, 10_000,
                          date(2025, 4, 6))

        out = {c.display_name: c.contributed_pence
               for c in contributions.member_contributions(
                   session, household.id, start=START, end=END)}
        assert out["Ada"] == 10_000  # only the shared-dest leg counts

    def test_shared_source_not_a_member(
            self, session: Session, household: Household) -> None:
        _member(session, household, "Ada")
        joint = _account(session, household, "Joint", None)
        shared2 = _account(session, household, "Bills", None)
        _matched_transfer(session, household, joint, shared2,
                          12_000, date(2025, 4, 5))
        out = {c.display_name: c.contributed_pence
               for c in contributions.member_contributions(
                   session, household.id, start=START, end=END)}
        assert out["Ada"] == 0

    def test_both_legs_matched_counts_once(
            self, session: Session, household: Household) -> None:
        """Matching both the in- and out-leg movements of one
        transfer must not double-count it."""
        ada = _member(session, household, "Ada")
        pa = _account(session, household, "Ada acct", ada)
        joint = _account(session, household, "Joint", None)
        m = _matched_transfer(session, household, pa, joint, 25_000,
                              date(2025, 4, 5))
        # also match the OUT leg movement on Ada's account
        out_leg = session.query(Transaction).filter(
            Transaction.transfer_id ==
            m.transaction.transfer_id,
            Transaction.amount_pence < 0).one()
        mv2 = ExpectedMovement(
            household_id=household.id, account_id=pa.id,
            expected_date=date(2025, 4, 5), amount_pence=-25_000,
            label="share out", confidence=Confidence.scheduled,
            source_type=ExpectedSource.transfer_rule, source_id=2)
        session.add(mv2)
        session.flush()
        session.add(ExpectedMatch(movement_id=mv2.id,
                                  transaction_id=out_leg.id,
                                  matched_pence=25_000))
        session.flush()
        out = {c.display_name: c.contributed_pence
               for c in contributions.member_contributions(
                   session, household.id, start=START, end=END)}
        assert out["Ada"] == 25_000

    def test_zeroed_members_listed(
            self, session: Session, household: Household) -> None:
        _member(session, household, "Ada")
        _member(session, household, "Bob")
        out = contributions.member_contributions(
            session, household.id, start=START, end=END)
        assert {(c.display_name, c.contributed_pence)
                for c in out} == {("Ada", 0), ("Bob", 0)}

    def test_unmatched_scheduled_does_not_count(
            self, session: Session, household: Household) -> None:
        """A scheduled-but-unmatched expectation is not a
        contribution."""
        _member(session, household, "Ada")
        joint = _account(session, household, "Joint", None)
        session.add(ExpectedMovement(
            household_id=household.id, account_id=joint.id,
            expected_date=date(2025, 4, 5), amount_pence=50_000,
            label="scheduled", confidence=Confidence.scheduled,
            source_type=ExpectedSource.transfer_rule, source_id=9))
        session.flush()
        out = {c.display_name: c.contributed_pence
               for c in contributions.member_contributions(
                   session, household.id, start=START, end=END)}
        assert out["Ada"] == 0


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_by_member_endpoint(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        res = client.get(
            "/api/v1/expected-movements/contributions/by-member",
            params={"start": "2025-04-01", "end": "2025-04-30"},
            headers=h)
        assert res.status_code == 200
        assert res.json() == []
