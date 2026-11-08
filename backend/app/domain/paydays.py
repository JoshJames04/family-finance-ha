"""Payday rules: fixed day, last working day, Nth weekday (issue #73).

A working day = Monday–Friday and not a UK bank holiday. Holidays come
from the embedded versioned table ``data/uk_bank_holidays.json``
(GOV.UK England & Wales) — new years are added by updating the data
file only, never by changing code.
"""

from __future__ import annotations

import calendar
import enum
import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "uk_bank_holidays.json"
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
             "sat": 5, "sun": 6}


class ShiftPolicy(enum.StrEnum):
    previous = "previous"  # shift back to the previous working day
    next = "next"          # shift forward to the next working day
    none = "none"          # never shift — pay lands on the literal date


class RuleKind(enum.StrEnum):
    fixed_day = "fixed_day"            # e.g. the 25th of each month
    last_day = "last_day"              # last calendar day of the month
    last_working_day = "last_working_day"
    nth_weekday = "nth_weekday"        # e.g. 2nd Friday (weekday 0–6, nth 1–5; -1 = last)
    weekly = "weekly"                  # every <weekday> — weekly pay (#304)
    nightly = "nightly"                # paid on the night — cash-in-hand (#304)


@dataclass(frozen=True)
class PaydayRule:
    kind: RuleKind
    day: int | None = None        # fixed_day: 1–31 (clamped to month length)
    weekday: int | None = None    # nth_weekday: 0=Mon … 6=Sun
    nth: int | None = None        # nth_weekday: 1–5, or -1 for last
    adjust: ShiftPolicy = ShiftPolicy.previous


@dataclass(frozen=True)
class HolidayTable:
    version: str
    dates: frozenset[date]

    def is_holiday(self, d: date) -> bool:
        return d in self.dates


@lru_cache(maxsize=1)
def load_holidays() -> HolidayTable:
    """Parse the embedded GOV.UK table (cached per process)."""
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return HolidayTable(
        version=str(raw["version"]),
        dates=frozenset(date.fromisoformat(k) for k in raw["dates"]),
    )


def is_working_day(d: date, holidays: HolidayTable | None = None) -> bool:
    hol = holidays or load_holidays()
    return d.weekday() < 5 and not hol.is_holiday(d)


def adjust(d: date, policy: ShiftPolicy,
           holidays: HolidayTable | None = None) -> date:
    """Shift `d` per policy until it lands on a working day."""
    if policy is ShiftPolicy.none or is_working_day(d, holidays):
        return d
    step = -1 if policy is ShiftPolicy.previous else 1
    while not is_working_day(d, holidays):
        d += timedelta(days=step)
    return d


def _fixed(year: int, month: int, day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    last = calendar.monthrange(year, month)[1]
    days = [d for d in range(1, last + 1)
            if date(year, month, d).weekday() == weekday]
    if nth > 0:
        return date(year, month, days[min(nth, len(days)) - 1])
    return date(year, month, days[nth])  # -1 = last occurrence


def nominal_date(rule: PaydayRule, year: int, month: int,
                 holidays: HolidayTable | None = None) -> date:
    """The unadjusted date the rule selects in the given month."""
    if rule.kind is RuleKind.fixed_day:
        assert rule.day is not None
        return _fixed(year, month, rule.day)
    if rule.kind is RuleKind.last_day:
        return _fixed(year, month, 31)
    if rule.kind is RuleKind.nth_weekday:
        assert rule.weekday is not None and rule.nth is not None
        return _nth_weekday(year, month, rule.weekday, rule.nth)
    if rule.kind is RuleKind.nightly:
        # paid the day worked — the month's pay date is the period end
        return _fixed(year, month, 31)
    # weekly has no single monthly pay date; prepare-year iterates
    # ISO weeks and calls weekly_pay_date directly.
    if rule.kind is RuleKind.weekly:
        return weekly_pay_date(rule, year, month)
    # last_working_day: nominal = last calendar day; the adjust pass
    # would only ever move backwards, so resolve directly.
    d = _fixed(year, month, 31)
    while not is_working_day(d, holidays):
        d -= timedelta(days=1)
    return d


def weekly_pay_date(rule: PaydayRule, year: int, month: int) -> date:
    """Last occurrence of rule.weekday inside (year, month)."""
    assert rule.weekday is not None
    d = date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() != rule.weekday:
        d -= timedelta(days=1)
    return d


def pay_date(rule: PaydayRule, year: int, month: int,
             holidays: HolidayTable | None = None) -> date:
    """Nominal rule date adjusted by the rule's shift policy."""
    d = nominal_date(rule, year, month, holidays)
    return adjust(d, rule.adjust, holidays)


def next_payday(rule: PaydayRule, after: date,
                holidays: HolidayTable | None = None) -> date:
    """First pay date strictly after `after` (per-member usable)."""
    y, m = after.year, after.month
    for _ in range(25):  # bounded — a rule must land within 2 years
        d = pay_date(rule, y, m, holidays)
        if d > after:
            return d
        m += 1
        if m > 12:
            y, m = y + 1, 1
    raise AssertionError("unreachable: payday rules always land")
