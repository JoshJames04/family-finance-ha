"""Issue #80 — payroll calendar: cut-off + pay-date assignment."""

from __future__ import annotations

from datetime import date

from app.domain.pay_calendar import (
    PayrollCalendar,
    parse_calendar,
    period,
    period_for_entry,
    periods_between,
    serialize_calendar,
)
from app.domain.paydays import PaydayRule, RuleKind, ShiftPolicy

# The workbook's rule: cut-off 15th, pay ~28th.
WORKBOOK = PayrollCalendar(
    cutoff_day=15,
    pay_rule=PaydayRule(RuleKind.fixed_day, day=28,
                        adjust=ShiftPolicy.previous))


class TestAttribution:
    def test_cutoff_day_boundary_matrix(self) -> None:
        """AC: overtime on the 16th lands in the next period."""
        cases = [
            (date(2026, 4, 15), (2026, 4)),   # cut-off day → this month
            (date(2026, 4, 16), (2026, 5)),   # day after → next
            (date(2026, 4, 1), (2026, 4)),
            (date(2026, 4, 30), (2026, 5)),
            (date(2026, 12, 31), (2027, 1)),  # year boundary
        ]
        for d, (y, m) in cases:
            p = period_for_entry(WORKBOOK, d)
            assert (p.year, p.month) == (y, m), f"{d} → {(p.year, p.month)}"

    def test_every_date_exactly_one_period(self) -> None:
        """Never lost, never double: partition over a full year."""
        d, overlaps, gaps = date(2026, 1, 1), 0, 0
        prev_cutoff = None
        while d <= date(2026, 12, 31):
            p = period_for_entry(WORKBOOK, d)
            assert p.start <= d <= p.cutoff
            if prev_cutoff is not None and p.start == prev_cutoff:
                overlaps += 1
            prev_cutoff = p.cutoff
            from datetime import timedelta
            d += timedelta(days=1)
        assert overlaps == 0 and gaps == 0

    def test_short_month_cutoff_clamps(self) -> None:
        cal = PayrollCalendar(
            cutoff_day=31,
            pay_rule=PaydayRule(RuleKind.last_day))
        p = period_for_entry(cal, date(2026, 2, 28))
        assert p.cutoff == date(2026, 2, 28)
        assert p.start == date(2026, 2, 1)  # prev cutoff was Jan 31

    def test_period_dates(self) -> None:
        p = period(WORKBOOK, 2026, 5)
        assert p.start == date(2026, 4, 16)
        assert p.cutoff == date(2026, 5, 15)


class TestPayDates:
    def test_weekend_adjustment(self) -> None:
        """AC: pay date on weekend/holiday adjusts per payday rules."""
        # May 2026: 28th is Thursday — no adjustment needed
        assert period(WORKBOOK, 2026, 5).pay_date == date(2026, 5, 28)
        # Feb 2026: 28th is Saturday → previous working Fri 27th
        assert period(WORKBOOK, 2026, 2).pay_date == date(2026, 2, 27)

    def test_holiday_adjustment(self) -> None:
        cal = PayrollCalendar(
            cutoff_day=20,
            pay_rule=PaydayRule(RuleKind.fixed_day, day=25,
                                adjust=ShiftPolicy.next))
        # Dec 2026: 25th is Christmas → next working = Mon 28th? no —
        # 26th Sat, 27th Sun, 28th is Boxing substitute BH → Tue 29th
        assert period(cal, 2026, 12).pay_date == date(2026, 12, 29)


class TestRanges:
    def test_periods_between(self) -> None:
        ps = periods_between(WORKBOOK, date(2026, 3, 1),
                             date(2026, 6, 30))
        # Jun 16–30 entries belong to the July run — it must appear
        assert [(p.year, p.month) for p in ps] == [
            (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7)]

    def test_serialization_round_trip(self) -> None:
        raw = serialize_calendar(WORKBOOK)
        cal2 = parse_calendar(raw)
        assert cal2 == WORKBOOK
        # usable for employment.payday_rule column
        assert period_for_entry(cal2, date(2026, 4, 16)
                                ).month == 5
