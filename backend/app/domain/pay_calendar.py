"""Payroll calendar: cut-off rules + pay-date assignment (issue #80).

A payroll period is labelled by its *pay month*: entries dated on or
before the month's cut-off day belong to it; entries after cut-off fall
into the next month's period (never lost, never double-counted). Pay
dates resolve through :mod:`app.domain.paydays` so weekend/bank-holiday
adjustment is identical to standalone payday rules.

Serialisation produces compact JSON suitable for
``employment.payday_rule`` — one column carries the whole calendar.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.domain.paydays import (
    PaydayRule,
    RuleKind,
    ShiftPolicy,
    pay_date,
)


@dataclass(frozen=True)
class PayrollCalendar:
    cutoff_day: int                 # 1–31, clamped to month length
    pay_rule: PaydayRule


@dataclass(frozen=True)
class PayrollPeriod:
    year: int
    month: int                      # pay month (the period label)
    start: date                     # inclusive
    cutoff: date                    # inclusive
    pay_date: date                  # adjusted per pay_rule


def _cutoff(cal: PayrollCalendar, year: int, month: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(cal.cutoff_day, last))


def period(cal: PayrollCalendar, year: int, month: int) -> PayrollPeriod:
    """The period labelled `year`-`month`: (prev month cutoff, cutoff]."""
    c = _cutoff(cal, year, month)
    prev_m = month - 1 or 12
    prev_y = year - 1 if month == 1 else year
    start = _cutoff(cal, prev_y, prev_m)
    # entries strictly after the previous cut-off open this period
    return PayrollPeriod(
        year=year, month=month, start=start + timedelta(days=1),
        cutoff=c, pay_date=pay_date(cal.pay_rule, year, month))


def period_for_entry(cal: PayrollCalendar,
                     entry_date: date) -> PayrollPeriod:
    """Attribution: on/before this month's cut-off → this month; after
    → next month. Every date maps to exactly one period."""
    c = _cutoff(cal, entry_date.year, entry_date.month)
    if entry_date <= c:
        return period(cal, entry_date.year, entry_date.month)
    nxt_m = entry_date.month + 1
    nxt_y = entry_date.year + (1 if nxt_m > 12 else 0)
    nxt_m = nxt_m if nxt_m <= 12 else 1
    return period(cal, nxt_y, nxt_m)


def periods_between(cal: PayrollCalendar, start: date,
                    end: date) -> list[PayrollPeriod]:
    """All periods whose [start, cutoff] window intersects [start,end]."""
    first = period_for_entry(cal, start)
    out = [first]
    while out[-1].cutoff < end:
        m = out[-1].month + 1
        y = out[-1].year + (1 if m > 12 else 0)
        m = m if m <= 12 else 1
        out.append(period(cal, y, m))
    return out


def serialize_rule(rule: PaydayRule) -> str:
    return json.dumps({
        "kind": str(rule.kind), "day": rule.day,
        "weekday": rule.weekday, "nth": rule.nth,
        "adjust": str(rule.adjust),
    }, separators=(",", ":"), sort_keys=True)


def parse_rule(raw: str) -> PaydayRule:
    d: dict[str, Any] = json.loads(raw)
    if "pay_rule" in d:
        d = d["pay_rule"]
    default_adjust = "none" if d.get("kind") in {
        RuleKind.nth_weekday.value, RuleKind.weekly.value,
    } else "previous"
    rule = PaydayRule(
        kind=RuleKind(d["kind"]), day=d.get("day"),
        weekday=d.get("weekday"), nth=d.get("nth"),
        adjust=ShiftPolicy(d.get("adjust", default_adjust)))
    if rule.kind is RuleKind.fixed_day and (
            not isinstance(rule.day, int) or not 1 <= rule.day <= 31):
        raise ValueError("fixed payday day must be between 1 and 31")
    if rule.kind in (RuleKind.nth_weekday, RuleKind.weekly) and (
            not isinstance(rule.weekday, int)
            or not 0 <= rule.weekday <= 6):
        raise ValueError("payday weekday must be between 0 and 6")
    if rule.kind is RuleKind.nth_weekday \
            and (rule.nth not in {-1, 1, 2, 3, 4, 5}):
        raise ValueError("payday nth must be -1 or between 1 and 5")
    return rule


def serialize_calendar(cal: PayrollCalendar) -> str:
    return json.dumps({
        "cutoff_day": cal.cutoff_day,
        "pay_rule": json.loads(serialize_rule(cal.pay_rule)),
    }, separators=(",", ":"), sort_keys=True)


def parse_calendar(raw: str) -> PayrollCalendar:
    d: dict[str, Any] = json.loads(raw)
    if not isinstance(d.get("cutoff_day"), int) or not 1 <= d["cutoff_day"] <= 31:
        raise ValueError("payroll cutoff must be between 1 and 31")
    return PayrollCalendar(
        cutoff_day=d["cutoff_day"],
        pay_rule=parse_rule(json.dumps(d.get("pay_rule", d))))
