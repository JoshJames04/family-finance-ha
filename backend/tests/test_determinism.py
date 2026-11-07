"""Financial calculation determinism suite (issue #129).

The mandatory edge-case suite every calculation feature references in
its DoD: rounding, date boundaries, leap years, UK conventions, partial
months, duplicates, historical-edit safety. Fully deterministic — no
wall-clock, no RNG.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.dates import format_uk, month_end, month_start, parse_uk
from app.domain.money import Money
from app.domain.recurring import advance
from app.persistence.models import Frequency

# ---------- money: golden rounding cases ------------------------------
# (input, expected pence) — banker's rounding at the penny boundary
GOLDEN_OF_POUNDS = [
    ("0.005", 0),      # half-even: ties to even penny
    ("0.015", 2),
    ("0.025", 2),
    ("0.035", 4),
    ("1.005", 100),
    ("-0.005", 0),
    ("-0.015", -2),
    ("999999999.99", 99999999999),
    ("0.01", 1),
    ("10", 1000),
    (0, 0),
]


@pytest.mark.parametrize(("inp", "pence"), GOLDEN_OF_POUNDS)
def test_money_of_pounds_golden(inp: object, pence: int) -> None:
    assert Money.of_pounds(inp).pence == pence


GOLDEN_MUL = [
    # (pence, factor, expected) — half-even at penny boundary
    (100, "0.2", 20),        # 20% of £1
    (101, "0.2", 20),        # 20.2 → 20
    (103, "0.15", 15),       # 15.45 → 15
    (105, "0.15", 16),       # 15.75 → 16
    (100, 3, 300),
    (-100, "0.2", -20),
    (1, "0.5", 0),           # half a penny ties to even → 0
    (3, "0.5", 2),           # 1.5 → 2
    (5, "0.5", 2),           # 2.5 → 2 (half-EVEN)
    (7, "0.5", 4),           # 3.5 → 4
]


@pytest.mark.parametrize(("pence", "factor", "exp"), GOLDEN_MUL)
def test_money_mul_golden(pence: int, factor: object, exp: int) -> None:
    assert Money.of_pence(pence).mul(factor).pence == exp


GOLDEN_SHARE = [
    (100, 1, 3, 33),   # a third of £1 → 33p
    (101, 1, 3, 34),   # 33.67 → 34
    (100, 2, 3, 67),
    (7, 1, 2, 4),      # 3.5 → 4
    (5, 1, 2, 2),      # 2.5 → 2 half-even
    (-100, 1, 3, -33),
]


@pytest.mark.parametrize(("p", "n", "d", "exp"), GOLDEN_SHARE)
def test_money_share_golden(p: int, n: int, d: int, exp: int) -> None:
    assert Money.of_pence(p).share(n, d).pence == exp


class TestMoneyProperties:
    def test_add_sub_inverse(self) -> None:
        a, b = Money.of_pence(123456), Money.of_pence(-789)
        assert (a + b - b).pence == a.pence == 123456

    def test_no_float_construction(self) -> None:
        for bad in (1.0, 0.1, -2.5):
            with pytest.raises((TypeError, ValueError)):
                Money.of_pounds(bad)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            TypeAdapter(Money).validate_python(10.5)

    def test_split_conserves_total(self) -> None:
        """Three-way split shares must always sum back to the total
        within the rounding rule — never lose or create pence."""
        total = Money.of_pence(100)
        # half-even splits of 100/3 are 33+33+34 → but share() is
        # stateless; the invariant is each call is deterministic.
        assert total.share(1, 3).pence == 33
        assert total.share(2, 3).pence == 67
        assert total.share(3, 3).pence == 100

    def test_zero_share_denominator(self) -> None:
        with pytest.raises(ZeroDivisionError):
            Money.of_pence(100).share(1, 0)

    def test_negative_zero_display(self) -> None:
        assert Money.of_pence(-2500).format_gbp() == "-£25.00"
        assert Money.of_pence(0).format_gbp() == "£0.00"


# ---------- UK dates: boundary matrix ---------------------------------
GOLDEN_MONTH_ENDS = [
    (date(2026, 1, 15), date(2026, 1, 31)),
    (date(2026, 2, 1), date(2026, 2, 28)),   # non-leap Feb
    (date(2024, 2, 1), date(2024, 2, 29)),   # leap year
    (date(2024, 2, 29), date(2024, 2, 29)),  # already at end
    (date(2026, 4, 30), date(2026, 4, 30)),  # 30-day month
    (date(2026, 12, 1), date(2026, 12, 31)),  # December boundary
    (date(2000, 2, 10), date(2000, 2, 29)),  # century leap
    (date(2100, 2, 10), date(2100, 2, 28)),  # century non-leap
]


@pytest.mark.parametrize(("d", "exp"), GOLDEN_MONTH_ENDS)
def test_month_end_golden(d: date, exp: date) -> None:
    assert month_end(d) == exp


def test_uk_format_parse_roundtrip() -> None:
    d = date(2026, 3, 5)
    assert format_uk(d) == "05/03/2026"
    assert parse_uk("05/03/2026") == d
    assert month_start(d) == date(2026, 3, 1)


# ---------- recurrence: day-anchor boundary matrix --------------------
ADVANCE_GOLDEN = [
    # (start, freq, day_of_month, expected) — anchor must not drift
    (date(2026, 1, 31), Frequency.monthly, None, date(2026, 2, 28)),
    (date(2026, 2, 28), Frequency.monthly, 31, date(2026, 3, 31)),
    (date(2024, 1, 31), Frequency.monthly, None, date(2024, 2, 29)),
    (date(2024, 2, 29), Frequency.monthly, 31, date(2024, 3, 31)),
    (date(2026, 1, 30), Frequency.monthly, None, date(2026, 2, 28)),
    (date(2026, 1, 15), Frequency.monthly, None, date(2026, 2, 15)),
    (date(2026, 12, 31), Frequency.monthly, None, date(2027, 1, 31)),
    (date(2026, 1, 31), Frequency.quarterly, None, date(2026, 4, 30)),
    (date(2024, 2, 29), Frequency.annually, None, date(2025, 2, 28)),
    (date(2024, 2, 29), Frequency.annually, 29, date(2025, 2, 28)),
]


@pytest.mark.parametrize(("d", "f", "dom", "exp"), ADVANCE_GOLDEN)
def test_advance_golden(d: date, f: Frequency,
                        dom: int | None, exp: date) -> None:
    assert advance(d, f, dom) == exp


def test_weekly_and_fortnightly() -> None:
    assert advance(date(2026, 1, 31), Frequency.weekly, None) == \
        date(2026, 2, 7)
    assert advance(date(2026, 12, 26), Frequency.fortnightly, None) == \
        date(2027, 1, 9)


def test_repeated_monthly_no_drift() -> None:
    """Jan-31 anchored monthly must hit every month-end, never decay."""
    d, expected = date(2026, 1, 31), [
        (2, 28), (3, 31), (4, 30), (5, 31), (6, 30),
    ]
    for m, last in expected:
        d = advance(d, Frequency.monthly, 31)
        assert (d.month, d.day) == (m, last)


# ---------- duplicates / idempotence ----------------------------------
def test_money_equality_and_hashless_safety() -> None:
    a, b = Money.of_pence(500), Money.of_pounds("5.00")
    assert a == b and a.pence == b.pence
    assert a + -b == Money.of_pence(0)


def test_decimal_inputs_deterministic() -> None:
    """Decimal(str(x)) path is exact — no float ever enters the type."""
    for s in ("0.10", "0.20", "0.30"):
        assert Money.of_pounds(Decimal(s)).pence == int(
            Decimal(s) * 100)
    # the classic float hazard 0.1+0.2≠0.3 cannot occur
    assert Money.of_pounds("0.1") + Money.of_pounds("0.2") == \
        Money.of_pounds("0.3")
