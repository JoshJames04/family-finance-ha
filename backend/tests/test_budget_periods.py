"""Personal monthly plan windows use planned paydays without rewriting plans."""

import calendar
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app.domain.budget_periods import (
    resolve_period,
    selected_anchor,
    selected_day,
    set_anchor,
    set_day,
    source_choices,
)
from app.persistence.models import AppMeta, Budget, Employment, SalaryRevision, UserProfile


@pytest.fixture()
def profile(session, household):
    row = UserProfile(household_id=household.id, display_name="Member")
    session.add(row)
    session.flush()
    return row


def employment(session, profile, **kwargs):
    row = Employment(
        household_id=profile.household_id, profile_id=profile.id,
        employer=kwargs.pop("employer", "Employer"),
        jurisdiction="england_wales", contracted_hours=37.5,
        payday_rule=kwargs.pop("payday_rule", "last_friday"), **kwargs)
    session.add(row)
    session.flush()
    return row


def budget(profile, year=2026, month=11, **kwargs):
    values = dict(household_id=profile.household_id, owner_profile_id=profile.id,
                  name="Plan", period_type="monthly", start_date=date(year, month, 1),
                  end_date=date(year, month, calendar.monthrange(year, month)[1]))
    values.update(kwargs)
    return Budget(**values)


def test_november_and_october_share_one_adjusted_boundary(session, profile):
    emp = employment(session, profile)
    october = resolve_period(session, budget(profile, month=10), basis="pay_cycle")
    november = resolve_period(session, budget(profile), basis="pay_cycle")
    assert (october.start, october.end) == (date(2026, 9, 25), date(2026, 10, 29))
    assert (november.start, november.end) == (date(2026, 10, 30), date(2026, 11, 26))
    assert october.end + timedelta(days=1) == november.start
    assert november.employment_id == emp.id and november.warning is None


@pytest.mark.parametrize(("year", "month", "rule", "start", "end"), [
    (2026, 11, '{"kind":"fixed_day","day":24}', date(2026, 10, 23), date(2026, 11, 23)),
    (2026, 11, '{"kind":"fixed_day","day":25}', date(2026, 10, 23), date(2026, 11, 24)),
    (2026, 6, '{"kind":"fixed_day","day":25}', date(2026, 5, 22), date(2026, 6, 24)),
    (2026, 1, '{"kind":"fixed_day","day":25}', date(2025, 12, 24), date(2026, 1, 22)),
    (2024, 3, '{"kind":"last_day","adjust":"none"}', date(2024, 2, 29), date(2024, 3, 30)),
])
def test_adjusted_weekend_holiday_leap_and_year_boundaries(
        session, profile, year, month, rule, start, end):
    employment(session, profile, payday_rule=rule)
    window = resolve_period(session, budget(profile, year, month), basis="pay_cycle")
    assert (window.start, window.end) == (start, end)


def test_consecutive_historical_months_have_no_gaps_or_overlaps(session, profile):
    employment(session, profile, payday_rule='{"kind":"last_working_day"}')
    previous = None
    for year in (2025, 2026):
        for month in range(1, 13):
            window = resolve_period(session, budget(profile, year, month), basis="pay_cycle")
            assert window.start <= window.end
            if previous is not None:
                assert previous.end + timedelta(days=1) == window.start
            previous = window


def test_calendar_shared_and_custom_leave_explicit_dates(session, profile):
    employment(session, profile)
    for row in [budget(profile), budget(profile, owner_profile_id=None),
                budget(profile, period_type="custom", start_date=date(2026, 11, 5))]:
        basis = "calendar" if row.owner_profile_id else "pay_cycle"
        window = resolve_period(session, row, basis=basis)
        assert (window.start, window.end, window.basis) == (
            row.start_date, row.end_date, "calendar")
    custom = budget(profile, period_type="custom")
    assert resolve_period(session, custom, basis="pay_cycle").start == custom.start_date


@pytest.mark.parametrize("rule", [None, "invalid", '{"kind":"weekly","weekday":4}',
                                   '{"kind":"nightly"}', '[]'])
def test_missing_invalid_and_nonmonthly_sources_warn(session, profile, rule):
    employment(session, profile, payday_rule=rule)
    window = resolve_period(session, budget(profile), basis="pay_cycle")
    assert window.basis == "calendar" and window.warning
    assert source_choices(session, profile.household_id, profile.id) == []


def test_ambiguous_jobs_require_scoped_selection_and_can_clear(session, profile):
    first = employment(session, profile)
    second = employment(session, profile, employer="Other")
    assert resolve_period(session, budget(profile), basis="pay_cycle").warning
    assert source_choices(session, profile.household_id, profile.id) == [
        {"employment_id": first.id, "employer": "Employer"},
        {"employment_id": second.id, "employer": "Other"}]
    set_anchor(session, profile.household_id, profile.id, second.id)
    assert selected_anchor(session, profile.household_id, profile.id) == second.id
    assert resolve_period(session, budget(profile), basis="pay_cycle").employment_id == second.id
    set_anchor(session, profile.household_id, profile.id, None)
    assert selected_anchor(session, profile.household_id, profile.id) is None
    assert resolve_period(session, budget(profile), basis="pay_cycle").warning


def test_anchor_rejects_other_member_household_missing_and_nonmonthly(session, profile):
    own = employment(session, profile)
    other = UserProfile(household_id=profile.household_id, display_name="Other")
    session.add(other)
    session.flush()
    foreign = employment(session, other)
    weekly = employment(session, profile, payday_rule='{"kind":"weekly","weekday":4}')
    for hid, pid, eid in [(profile.household_id, profile.id, foreign.id),
                          (profile.household_id + 1, profile.id, own.id),
                          (profile.household_id, profile.id, 99999),
                          (profile.household_id, profile.id, weekly.id)]:
        with pytest.raises(ValueError):
            set_anchor(session, hid, pid, eid)


def test_unique_historical_coverage_precedes_current_legacy_fallback(session, profile):
    old = employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                     end_date=date(2024, 12, 31))
    current = employment(session, profile, start_date=date(2025, 1, 1))
    historical = resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle")
    legacy = resolve_period(session, budget(profile, 2019, 11), basis="pay_cycle")
    assert historical.employment_id == old.id
    assert legacy.employment_id == current.id


def test_salary_revision_supplies_historical_start_and_conflicts_warn(session, profile):
    old = employment(session, profile, is_active=False, end_date=date(2024, 12, 31))
    session.add(SalaryRevision(employment_id=old.id, effective_date=date(2020, 1, 1),
                               annual_salary_pence=1))
    employment(session, profile, start_date=date(2025, 1, 1))
    session.flush()
    historical = resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle")
    assert historical.employment_id == old.id
    employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
               end_date=date(2024, 12, 31))
    assert resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle").warning


def test_explicit_source_preserves_history_and_stale_selection_warns(session, profile):
    old = employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                     end_date=date(2024, 12, 31))
    current = employment(session, profile, start_date=date(2025, 1, 1))
    set_anchor(session, profile.household_id, profile.id, current.id)
    window = resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle")
    assert window.employment_id == old.id
    current.payday_rule = None
    session.flush()
    assert resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle").warning


def test_job_change_resolves_each_funding_payday_and_preserves_continuity(session, profile):
    old = employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                     end_date=date(2026, 10, 31),
                     payday_rule='{"kind":"fixed_day","day":25}')
    new = employment(session, profile, start_date=date(2026, 11, 1),
                     payday_rule='{"kind":"fixed_day","day":28}')
    november = resolve_period(session, budget(profile), basis="pay_cycle")
    december = resolve_period(session, budget(profile, month=12), basis="pay_cycle")
    assert (november.start, november.end, november.employment_id) == (
        date(2026, 10, 23), date(2026, 11, 26), old.id)
    assert december.start == november.end + timedelta(days=1)
    assert december.employment_id == new.id


def test_selected_new_job_preserves_former_job_boundary(session, profile):
    old = employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                     end_date=date(2026, 10, 31),
                     payday_rule='{"kind":"fixed_day","day":25}')
    new = employment(session, profile, start_date=date(2026, 11, 1),
                     payday_rule='{"kind":"fixed_day","day":28}')
    set_anchor(session, profile.household_id, profile.id, new.id)
    window = resolve_period(session, budget(profile), basis="pay_cycle")
    assert (window.start, window.end, window.employment_id) == (
        date(2026, 10, 23), date(2026, 11, 26), old.id)


def test_selected_ended_job_cannot_anchor_future(session, profile):
    ended = employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                       end_date=date(2026, 9, 30))
    set_anchor(session, profile.household_id, profile.id, ended.id)
    window = resolve_period(session, budget(profile), basis="pay_cycle")
    assert window.basis == "calendar" and window.warning


def test_selected_source_legacy_fallback_does_not_hide_ambiguous_history(session, profile):
    current = employment(session, profile, start_date=date(2025, 1, 1))
    set_anchor(session, profile.household_id, profile.id, current.id)
    legacy = resolve_period(session, budget(profile, 2019, 11), basis="pay_cycle")
    assert legacy.employment_id == current.id
    for _ in range(2):
        employment(session, profile, is_active=False, start_date=date(2020, 1, 1),
                   end_date=date(2024, 12, 31))
    historical = resolve_period(session, budget(profile, 2024, 11), basis="pay_cycle")
    assert historical.basis == "calendar" and historical.warning


def test_no_source_warns_and_unsupported_basis_rejected(session, profile):
    window = resolve_period(session, budget(profile), basis="pay_cycle")
    assert window.basis == "calendar" and window.warning
    with pytest.raises(ValueError):
        resolve_period(session, budget(profile), basis="invalid")


@pytest.mark.parametrize("manual", [False, True])
def test_get_does_not_write_or_change_locked_plan(session, profile, manual):
    if manual:
        set_day(session, profile.household_id, profile.id, 11)
    else:
        employment(session, profile)
    row = budget(profile, status="locked")
    session.add(row)
    session.flush()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        resolve_period(session, row, basis="pay_cycle")
        source_choices(session, profile.household_id, profile.id)
        selected_anchor(session, profile.household_id, profile.id)
        selected_day(session, profile.household_id, profile.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not ({"INSERT", "UPDATE", "DELETE"} & set(statements))
    assert row.status == "locked" and row.start_date == date(2026, 11, 1)
    assert not session.dirty and not session.new


@pytest.mark.parametrize(("year", "month", "start", "end"), [
    (2026, 7, date(2026, 6, 11), date(2026, 7, 9)),
    (2026, 11, date(2026, 10, 9), date(2026, 11, 10)),
    (2019, 11, date(2019, 10, 11), date(2019, 11, 10)),
])
def test_manual_payday_needs_no_employment_and_preserves_historical_weekend_rule(
        session, profile, year, month, start, end):
    set_day(session, profile.household_id, profile.id, 11)
    assert selected_day(session, profile.household_id, profile.id) == 11
    window = resolve_period(session, budget(profile, year, month), basis="pay_cycle")
    assert (window.start, window.end, window.basis, window.employment_id, window.warning) == (
        start, end, "pay_cycle", None, None)
    assert source_choices(session, profile.household_id, profile.id) == []


def test_manual_and_employment_settings_clear_each_other_and_auto_clears_all(session, profile):
    emp = employment(session, profile)
    hid, pid = profile.household_id, profile.id
    set_anchor(session, hid, pid, emp.id)
    set_day(session, hid, pid, 11)
    assert selected_anchor(session, hid, pid) is None
    set_anchor(session, hid, pid, emp.id)
    assert selected_day(session, hid, pid) is None
    set_day(session, hid, pid, 11)
    set_anchor(session, hid, pid, None)
    assert selected_day(session, hid, pid) is None
    set_anchor(session, hid, pid, emp.id)
    set_day(session, hid, pid, None)
    assert selected_anchor(session, hid, pid) is None


@pytest.mark.parametrize("day", [0, 32, -1, True, 11.5, "11"])
def test_manual_payday_rejects_invalid_day_without_changing_settings(session, profile, day):
    set_day(session, profile.household_id, profile.id, 11)
    with pytest.raises(ValueError):
        set_day(session, profile.household_id, profile.id, day)
    assert selected_day(session, profile.household_id, profile.id) == 11


def test_manual_payday_validates_member_household(session, profile):
    for hid, pid in [(profile.household_id + 1, profile.id),
                     (profile.household_id, profile.id + 1000)]:
        with pytest.raises(ValueError):
            set_day(session, hid, pid, 11)


def test_invalid_stored_manual_payday_warns_instead_of_auto_source(session, profile):
    employment(session, profile)
    session.add(AppMeta(key=f"budget_pay_cycle_day:{profile.household_id}:{profile.id}",
                        value="invalid"))
    session.flush()
    window = resolve_period(session, budget(profile), basis="pay_cycle")
    assert window.basis == "calendar" and window.warning
