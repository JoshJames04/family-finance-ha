"""Employer bonuses share the real payroll and effective-dated holdings."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain import bonuses, payroll, payrun
from app.persistence.models import Employment, EmploymentBonus, PayrollRun, UserProfile
from tests.payroll_helpers import confirm


@pytest.fixture()
def employer(session, household):
    owner = UserProfile(household_id=household.id, display_name="Owner")
    session.add(owner)
    session.flush()
    emp = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Example",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        payday_rule='{"kind":"fixed_day","day":28,"adjust":"previous"}',
    )
    session.add(emp)
    session.flush()
    payroll.add_salary_revision(
        session, emp, date(2025, 1, 1), 6_000_000, shares_owned=Decimal("250")
    )
    payroll.add_tax_code_revision(session, emp, date(2025, 1, 1), "1257L")
    return emp


def test_bonus_parts_and_multiple_entries_share_one_payroll(session, employer):
    first = bonuses.save(
        session,
        employer,
        pay_month=date(2025, 4, 1),
        label="Annual",
        salary_percentage=Decimal("10"),
        share_value_pence=1500,
        manual_pence=10_000,
    )
    assert first.salary_bonus_pence == 600_000
    assert first.share_bonus_pence == 375_000
    assert first.gross_pence == 985_000
    run = session.scalar(select(PayrollRun))
    assert run.planned_bonus_pence == 985_000
    net = run.net_pence
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), label="Extra", manual_pence=50_000)
    assert len(session.scalars(select(PayrollRun)).all()) == 1
    assert run.planned_bonus_pence == 1_035_000
    assert net < run.net_pence < net + 50_000
    assert sum(c.forecast_pence for c in run.components) == run.net_pence


def test_salary_revision_inherits_effective_holding_unless_explicit_zero(session, employer):
    future = payroll.add_salary_revision(
        session, employer, date(2025, 8, 1), 7_000_000, shares_owned=Decimal("500")
    )
    middle = payroll.add_salary_revision(session, employer, date(2025, 5, 1), 6_500_000)
    assert middle.shares_owned == Decimal("250")
    zero = payroll.add_salary_revision(
        session, employer, date(2025, 9, 1), 7_500_000, shares_owned=Decimal("0")
    )
    assert zero.shares_owned == Decimal("0")
    assert future.shares_owned == Decimal("500")


def test_bonus_rounding_matches_the_money_policy():
    assert bonuses._pence(Decimal("50.5")) == 50
    assert bonuses._pence(Decimal("51.5")) == 52
    with pytest.raises(payroll.PayrollError):
        bonuses._pence(Decimal("1e100"))


def test_share_and_salary_basis_uses_adjusted_payday_and_snapshot(session, employer):
    payroll.add_tax_code_revision(session, employer, date(2025, 4, 1), "1257L M1")
    payroll.add_salary_revision(
        session, employer, date(2025, 6, 28), 9_000_000, shares_owned=Decimal("500")
    )
    # Saturday28 June is paid Friday27: the old salary/holding applies.
    bonus = bonuses.save(
        session,
        employer,
        pay_month=date(2025, 6, 1),
        salary_percentage=Decimal("1"),
        share_value_pence=1500,
    )
    assert bonus.pay_date == date(2025, 6, 27)
    assert bonus.shares_owned == Decimal("250")
    assert bonus.salary_basis_pence == 6_000_000
    payroll.add_salary_revision(
        session, employer, date(2025, 6, 1), 7_000_000, shares_owned=Decimal("300")
    )
    session.expire_all()
    saved = session.get(EmploymentBonus, bonus.id)
    assert saved.shares_owned == Decimal("250")


def test_locked_run_rejects_bonus_without_mutation(session, employer):
    original = payrun.generate(
        session, employer, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    confirm(session, original)
    with pytest.raises(payroll.PayrollError, match="reconciled"):
        bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100)
    assert session.scalars(select(EmploymentBonus)).all() == []


def test_delete_recalculates_existing_forecast_without_extra_salary(session, employer):
    bonus = bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100_000)
    run = session.scalar(select(PayrollRun))
    gross = run.gross_pence
    bonuses.remove(session, employer, bonus)
    assert run.planned_bonus_pence == 0
    assert run.gross_pence == gross - 100_000
    assert len(session.scalars(select(PayrollRun)).all()) == 1


def test_correction_preserves_planned_bonus_before_edit_and_delete(session, employer):
    bonus = bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100_000)
    original = session.scalar(select(PayrollRun))
    original_gross = original.gross_pence
    confirm(session, original)
    corrected = payrun.correct(session, original)
    assert corrected.planned_bonus_pence == 100_000
    with pytest.raises(payroll.PayrollError, match="correction|reconciled|protected"):
        bonuses.save(
            session, employer, pay_month=date(2025, 4, 1), manual_pence=200_000, existing=bonus
        )
    with pytest.raises(payroll.PayrollError, match="correction|reconciled|protected"):
        bonuses.remove(session, employer, bonus)
    assert corrected.gross_pence == original_gross
    assert corrected.planned_bonus_pence == 100_000
    assert original.gross_pence == original_gross


def test_incomplete_imported_components_cannot_recalculate_bonus(session, employer):
    from app.persistence.models import PayrollComponentKind

    run = payrun.generate(
        session,
        employer,
        start=date(2025, 4, 1),
        end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
    )
    original_gross, original_net = run.gross_pence, run.net_pence
    base = next(c for c in run.components if c.kind == PayrollComponentKind.base_salary)
    run.components.remove(base)
    session.delete(base)
    session.flush()
    with pytest.raises(payroll.PayrollError, match="component|incomplete"):
        bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100)
    assert (run.gross_pence, run.net_pence) == (original_gross, original_net)
    assert session.scalars(select(EmploymentBonus)).all() == []


def test_second_payroll_period_cannot_duplicate_monthly_bonuses(session, employer):
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100_000)
    original = session.scalar(select(PayrollRun))
    with pytest.raises(payroll.PayrollError, match="bonus|month"):
        payrun.generate(
            session,
            employer,
            start=date(2025, 4, 16),
            end=date(2025, 4, 30),
            pay_date=date(2025, 4, 28),
        )
    assert session.scalars(select(PayrollRun)).all() == [original]
    assert original.planned_bonus_pence == 100_000


def test_noncash_award_is_taxable_but_not_cash_and_survives_correction(session, employer):
    bonus = bonuses.save(session, employer, pay_month=date(2025, 4, 1), share_value_pence=1500)
    run = session.scalar(select(PayrollRun))
    cash_gross, cash_net = run.gross_pence, run.net_pence
    employer.share_payment_method = "payroll_noncash"
    bonuses.save(
        session, employer, pay_month=date(2025, 4, 1), share_value_pence=1500, existing=bonus
    )
    assert bonus.noncash_pence == 375_000
    assert run.gross_pence == cash_gross
    assert run.net_pence == cash_net - 375_000
    assert sum(c.forecast_pence for c in run.components) == run.net_pence
    confirm(session, run)
    replacement = payrun.correct(session, run)
    assert replacement.noncash_bonus_pence == 375_000
    with pytest.raises(payroll.PayrollError, match="correction|reconciled|protected"):
        bonuses.remove(session, employer, bonus)
    assert replacement.noncash_bonus_pence == 375_000
    assert sum(c.forecast_pence for c in replacement.components) == replacement.net_pence


def test_outside_payroll_share_proceeds_are_rejected_without_payroll_mutation(session, employer):
    employer.share_payment_method = "outside_payroll"
    with pytest.raises(payroll.PayrollError, match="outside payroll"):
        bonuses.save(session, employer, pay_month=date(2025, 4, 1), share_value_pence=1500)
    assert session.scalars(select(EmploymentBonus)).all() == []
    assert session.scalars(select(PayrollRun)).all() == []
    # Ordinary cash bonuses still use payroll for this employer.
    entry = bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=10_000)
    assert entry.gross_pence == 10_000
    assert entry.noncash_pence == 0
