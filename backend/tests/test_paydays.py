"""Issue #73 — payday rules, UK bank holidays, weekend adjustment."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.domain.paydays import (
    HolidayTable,
    PaydayRule,
    RuleKind,
    ShiftPolicy,
    adjust,
    is_working_day,
    load_holidays,
    next_payday,
    pay_date,
)

DATA = (Path(__file__).resolve().parents[1] / "app" / "domain" / "data"
        / "uk_bank_holidays.json")


class TestHolidayTable:
    def test_known_uk_dates(self) -> None:
        hol = load_holidays()
        known = {
            date(2026, 1, 1),    # New Year's Day
            date(2026, 4, 3),    # Good Friday
            date(2026, 4, 6),    # Easter Monday
            date(2026, 5, 4),    # Early May
            date(2026, 5, 25),   # Spring
            date(2026, 8, 31),   # Summer
            date(2026, 12, 25),  # Christmas
            date(2026, 12, 28),  # Boxing Day substitute (26th is Sat)
            date(2027, 12, 27),  # Christmas substitute (25th is Sat)
            date(2027, 12, 28),  # Boxing substitute (26th is Sun)
        }
        assert known <= hol.dates

    def test_versioned_data_source(self) -> None:
        """AC: new years are data-only updates — the file carries a
        version stamp and every date entry is ISO-parseable."""
        raw = json.loads(DATA.read_text(encoding="utf-8"))
        assert raw["version"]
        for k in raw["dates"]:
            date.fromisoformat(k)

    def test_working_day(self) -> None:
        assert is_working_day(date(2026, 5, 6))       # Wed, not holiday
        assert not is_working_day(date(2026, 5, 9))   # Sat
        assert not is_working_day(date(2026, 5, 25))  # Spring BH


class TestRules:
    def test_fixed_day(self) -> None:
        r = PaydayRule(RuleKind.fixed_day, day=25)
        assert pay_date(r, 2026, 5) == date(2026, 5, 25).replace(
            day=22)  # Mon 25th is Spring BH → previous working Fri 22nd

    def test_fixed_day_clamps_short_month(self) -> None:
        r = PaydayRule(RuleKind.fixed_day, day=31)
        assert pay_date(r, 2026, 2) == date(2026, 2, 27)  # 28th is Sat

    def test_bank_holiday_monday_next_policy(self) -> None:
        """AC: payday on a bank-holiday Monday shifts correctly per
        policy — `next` lands Tuesday, `previous` lands Friday."""
        mon = PaydayRule(RuleKind.fixed_day, day=25,
                         adjust=ShiftPolicy.next)
        prev = PaydayRule(RuleKind.fixed_day, day=25,
                          adjust=ShiftPolicy.previous)
        assert pay_date(mon, 2026, 5) == date(2026, 5, 26)
        assert pay_date(prev, 2026, 5) == date(2026, 5, 22)

    def test_none_policy_never_shifts(self) -> None:
        r = PaydayRule(RuleKind.fixed_day, day=25,
                       adjust=ShiftPolicy.none)
        assert pay_date(r, 2026, 5) == date(2026, 5, 25)  # literal BH

    def test_last_working_day(self) -> None:
        r = PaydayRule(RuleKind.last_working_day)
        # Dec 2026: 31st is Thu (working) — Christmas hols don't reach it
        assert pay_date(r, 2026, 12) == date(2026, 12, 31)
        # Aug 2026: 31st is the Summer BH → previous working day Fri 28th
        assert pay_date(r, 2026, 8) == date(2026, 8, 28)

    def test_nth_weekday(self) -> None:
        second_fri = PaydayRule(RuleKind.nth_weekday, weekday=4, nth=2)
        assert pay_date(second_fri, 2026, 5) == date(2026, 5, 8)
        last_fri = PaydayRule(RuleKind.nth_weekday, weekday=4, nth=-1)
        assert pay_date(last_fri, 2026, 5) == date(2026, 5, 29)
        # 5th weekday clamps to last occurrence in 4-weekday months
        fifth_fri = PaydayRule(RuleKind.nth_weekday, weekday=4, nth=5)
        assert pay_date(fifth_fri, 2026, 2) == date(2026, 2, 27)

    def test_nth_weekday_on_holiday(self) -> None:
        good_fri = PaydayRule(RuleKind.nth_weekday, weekday=4, nth=1)
        # 1st Friday Apr 2026 = Good Friday → previous working Thu 2nd
        assert pay_date(good_fri, 2026, 4) == date(2026, 4, 2)


class TestSequences:
    def test_next_payday_rolls_months(self) -> None:
        r = PaydayRule(RuleKind.fixed_day, day=25)
        assert next_payday(r, date(2026, 5, 25)) == date(2026, 6, 25)
        assert next_payday(r, date(2026, 5, 10)) == date(2026, 5, 22)
        # over year boundary: Jan 25 2027 is a working Monday
        assert next_payday(r, date(2026, 12, 26)) == date(2027, 1, 25)

    def test_multi_member_rules_independent(self) -> None:
        """Two members' rules resolve independently on the same table."""
        josh = PaydayRule(RuleKind.last_working_day)
        partner = PaydayRule(RuleKind.nth_weekday, weekday=4, nth=-1)
        assert pay_date(josh, 2026, 4) == date(2026, 4, 30)
        assert pay_date(partner, 2026, 4) == date(2026, 4, 24)

    def test_custom_holiday_table(self) -> None:
        """A caller-supplied table (e.g. different nation) is honoured."""
        hol = HolidayTable(version="test", dates=frozenset(
            {date(2026, 5, 29)}))  # block the real last weekday
        r = PaydayRule(RuleKind.last_working_day)
        assert pay_date(r, 2026, 5, hol) == date(2026, 5, 28)


def test_adjust_is_idempotent() -> None:
    d = adjust(date(2026, 5, 25), ShiftPolicy.previous)
    assert adjust(d, ShiftPolicy.previous) == d
