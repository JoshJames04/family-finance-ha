"""Recurring commitments: scheduling, end conditions, materialise (issue #38)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.recurring import advance, materialise, occurrences
from app.persistence.models import (
    Account,
    AccountType,
    Frequency,
    Household,
    RecurringCommitment,
)


@pytest.fixture
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Bills",
                type=AccountType.bills, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add(a)
    session.flush()
    return a


def _rc(account: Account, **kw) -> RecurringCommitment:
    return RecurringCommitment(
        household_id=account.household_id, name="Council Tax",
        account_id=account.id,
        amount_pence=kw.pop("amount_pence", -15000),
        frequency=kw.pop("frequency", Frequency.monthly),
        next_date=kw.pop("next_date", date(2026, 1, 31)),
        **kw,
    )


class TestAdvance:
    @pytest.mark.parametrize("freq,start,expect", [
        ("weekly", "2026-01-31", "2026-02-07"),
        ("fortnightly", "2026-01-31", "2026-02-14"),
        ("monthly", "2026-01-31", "2026-02-28"),   # clamps
        ("monthly", "2024-01-31", "2024-02-29"),   # leap
        ("quarterly", "2026-11-30", "2027-02-28"), # wraps year + clamps
        ("annually", "2024-02-29", "2025-02-28"),  # 29 Feb -> 28
        ("annually", "2026-06-15", "2027-06-15"),
    ])
    def test_matrix(self, freq: str, start: str, expect: str) -> None:
        s = date.fromisoformat(start)
        assert advance(s, Frequency(freq), None).isoformat() == expect


class TestOccurrences:
    def test_end_date_cutoff(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, end_date=date(2026, 3, 31))
        assert list(occurrences(c, date(2026, 6, 30))) == [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]

    def test_occurrence_count(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, occurrence_count=2)
        assert len(list(occurrences(c, date(2027, 1, 1)))) == 2

    def test_open_ended(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        assert len(list(occurrences(c, date(2026, 4, 30)))) == 4


class TestMaterialise:
    def test_creates_pending_rows(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        session.add(c)
        session.flush()
        made = materialise(session, c, date(2026, 3, 31))
        session.commit()
        assert len(made) == 3
        assert all(t.status.value == "pending" for t in made)
        assert all(t.amount_pence == -15000 for t in made)
        assert c.generated_count == 3
        assert c.next_date == date(2026, 4, 30)  # 31st clamps

    def test_idempotent_rerun(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        session.add(c)
        session.flush()
        materialise(session, c, date(2026, 3, 31))
        session.commit()
        again = materialise(session, c, date(2026, 3, 31))
        assert again == []
        # but advancing the horizon produces only the new row
        more = materialise(session, c, date(2026, 4, 30))
        assert len(more) == 1

    def test_inactive_produces_nothing(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, active=False)
        session.add(c)
        session.flush()
        assert materialise(session, c, date(2026, 6, 30)) == []

    def test_formula_commitment_refused(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, amount_pence=None, formula="payroll.net")
        session.add(c)
        session.flush()
        with pytest.raises(ValueError, match="payroll"):
            materialise(session, c, date(2026, 3, 31))


class TestConstraints:
    @pytest.mark.parametrize("kw", [
        {"amount_pence": -100, "formula": "x"},          # both set
        {"amount_pence": None, "formula": None},         # neither
    ])
    def test_amount_xor_formula(
        self, session: Session, account: Account, kw: dict
    ) -> None:
        session.add(_rc(account, **kw))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_day_of_month_bounds(
        self, session: Session, account: Account
    ) -> None:
        session.add(_rc(account, day_of_month=32))
        with pytest.raises(IntegrityError):
            session.flush()
