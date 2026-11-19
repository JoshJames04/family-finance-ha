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


def test_bonus_recovers_missing_forecast_base_from_salary_revision(session, employer):
    from app.persistence.models import PayrollComponentKind

    run = payrun.generate(
        session, employer, start=date(2025, 4, 1), end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
    )
    original_gross = run.gross_pence
    base = next(c for c in run.components if c.kind == PayrollComponentKind.base_salary)
    run.components.remove(base)
    session.flush()

    bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100)

    assert run.gross_pence == original_gross + 100
    assert any(c.kind == PayrollComponentKind.base_salary for c in run.components)


def test_incomplete_imported_components_cannot_recalculate_bonus(session, employer):
    from app.persistence.models import PayrollComponentKind

    run = payrun.generate(
        session,
        employer,
        start=date(2025, 4, 1),
        end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
    )
    run.gross_pence += 100  # Legacy earnings not explained by the source salary.
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


@pytest.mark.parametrize("cutoff", [False, True])
@pytest.mark.parametrize("policy", ["annual_daily", "equal_monthly"])
def test_final_bonus_can_be_paid_after_leaving_and_edited_once(session, employer, cutoff, policy):
    employer.start_date = date(2025, 4, 1)
    employer.end_date = date(2025, 4, 20)
    employer.salary_policy = policy
    employer.deduction_mode = "gross_only"
    if cutoff:
        employer.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    month = date(2025, 5 if cutoff else 4, 1)
    entry = bonuses.save(session, employer, pay_month=month, manual_pence=10_000)
    run = session.scalar(select(PayrollRun))
    assert run.pay_date == date(2025, month.month, 28)
    assert run.period_end == date(2025, 4, 20)
    assert run.planned_bonus_pence == 10_000
    bonuses.save(session, employer, pay_month=month, manual_pence=20_000, existing=entry)
    assert run.planned_bonus_pence == 20_000
    assert len(session.scalars(select(PayrollRun)).all()) == 1
    bonuses.remove(session, employer, entry)
    assert run.planned_bonus_pence == 0
    with pytest.raises(payroll.PayrollError, match="outside"):
        bonuses.save(session, employer, pay_month=date(2025, 6, 1), manual_pence=100)


def test_final_cutoff_run_does_not_repeat_previous_month_bonus(session, employer):
    employer.start_date = date(2025, 4, 1)
    employer.end_date = date(2025, 4, 20)
    employer.deduction_mode = "gross_only"
    employer.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=10_000)
    final = payrun.generate(
        session, employer, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
    )
    assert final.period_end == date(2025, 4, 20)
    assert final.planned_bonus_pence == 0


def test_final_cutoff_correction_protects_bonus_from_edit_and_removal(session, employer):
    employer.start_date = date(2025, 4, 1)
    employer.end_date = date(2025, 4, 20)
    employer.deduction_mode = "gross_only"
    employer.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    entry = bonuses.save(session, employer, pay_month=date(2025, 5, 1), manual_pence=10_000)
    original = session.scalar(select(PayrollRun))
    confirm(session, original)
    replacement = payrun.correct(session, original)
    with pytest.raises(payroll.PayrollError, match="correction|protected"):
        bonuses.save(
            session, employer, pay_month=entry.pay_month, manual_pence=20_000, existing=entry
        )
    with pytest.raises(payroll.PayrollError, match="correction|protected"):
        bonuses.remove(session, employer, entry)
    assert entry.gross_pence == 10_000
    assert replacement.planned_bonus_pence == 10_000


def test_monthly_salary_starter_can_receive_bonus_after_overtime_cutoff(session, employer):
    employer.start_date = date(2025, 4, 20)
    employer.salary_policy = "equal_monthly"
    employer.deduction_mode = "gross_only"
    employer.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    entry = bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=10_000)
    run = session.scalar(select(PayrollRun))
    assert run.planned_bonus_pence == entry.gross_pence == 10_000
    assert run.gross_pence > entry.gross_pence


@pytest.mark.parametrize("change", ["shares", "notes", "unchanged"])
def test_nonpay_salary_edits_do_not_invalidate_forecasts(session, employer, change):
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), share_value_pence=1500)
    run = session.scalar(select(PayrollRun))
    revision = employer.salary_revisions[0]
    payroll.update_salary_revision(
        session,
        revision,
        effective_date=revision.effective_date,
        annual_pence=revision.annual_salary_pence,
        shares_owned=Decimal("300") if change == "shares" else revision.shares_owned,
        notes="Synthetic note" if change == "notes" else revision.notes,
    )
    assert not run.forecast_stale


@pytest.mark.parametrize("change", ["salary", "date"])
def test_pay_salary_edits_still_invalidate_forecasts(session, employer, change):
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=100)
    run = session.scalar(select(PayrollRun))
    revision = employer.salary_revisions[0]
    payroll.update_salary_revision(
        session,
        revision,
        effective_date=date(2025, 1, 2) if change == "date" else revision.effective_date,
        annual_pence=revision.annual_salary_pence + (10000 if change == "salary" else 0),
    )
    assert run.forecast_stale


def test_automatic_rebuild_scope_leaves_earlier_forecast_untouched(session, employer):
    from app.domain import payroll_rebuild

    for month, end in ((4, 30), (5, 31), (6, 30)):
        payrun.generate(
            session, employer, start=date(2025, month, 1), end=date(2025, month, end),
            pay_date=date(2025, month, 28),
        )

    start = date(2025, 5, 28)
    preview = payroll_rebuild.rebuild_forecasts(
        session, employer, apply=False, from_pay_date=start,
    )
    assert [change["pay_date"] for change in preview["changes"]] == ["2025-05-28", "2025-06-28"]
    applied = payroll_rebuild.rebuild_forecasts(
        session, employer, apply=True, fingerprint=preview["fingerprint"], from_pay_date=start,
    )
    assert applied["changes"] == preview["changes"]
