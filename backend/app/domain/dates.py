"""UK date conventions — dd/MM/yyyy display, ISO storage."""

from __future__ import annotations

from datetime import date, timedelta


def format_uk(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def parse_uk(s: str) -> date:
    day, month, year = (int(p) for p in s.split("/"))
    return date(year, month, day)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)
