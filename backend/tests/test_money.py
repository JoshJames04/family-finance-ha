"""Money type: rounding policy, arithmetic, pydantic boundary."""

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.dates import format_uk, month_end, parse_uk
from app.domain.money import Money


class Payload(BaseModel):
    amount: Money


class TestConstruction:
    def test_pence(self) -> None:
        assert Money.of_pence(12345).pence == 12345

    def test_pounds_str(self) -> None:
        assert Money.of_pounds("123.45").pence == 12345

    def test_pounds_decimal(self) -> None:
        assert Money.of_pounds(Decimal("0.01")).pence == 1

    def test_non_gbp_rejected(self) -> None:
        with pytest.raises(ValueError):
            Money(100, "USD")

    def test_bool_rejected(self) -> None:
        with pytest.raises(TypeError):
            Money(True)


class TestArithmetic:
    def test_add_sub(self) -> None:
        assert (Money.of_pence(100) + Money.of_pence(250)).pence == 350
        assert (Money.of_pence(100) - Money.of_pence(250)).pence == -150

    def test_neg(self) -> None:
        assert (-Money.of_pence(5)).pence == -5

    def test_ordering(self) -> None:
        assert Money.of_pence(1) < Money.of_pence(2)


class TestRoundingPolicy:
    """Half-even at the penny — pinned so payroll/tax maths is reproducible."""

    def test_overtime_1_5x_on_odd_pence(self) -> None:
        # £12.345/hr is not representable; take pence-exact base then multiply
        rate = Money.of_pence(1235)  # £12.35
        assert rate.mul("1.5").pence == 1852   # 1852.5 → 1852 (half-even down)
        rate2 = Money.of_pence(1245)  # £12.45
        assert rate2.mul("1.5").pence == 1868  # 1867.5 → 1868 (half-even up)

    def test_percentage_deduction(self) -> None:
        gross = Money.of_pence(285000)  # £2,850
        assert gross.mul("0.20").pence == 57000
        assert gross.mul("0.08").pence == 22800

    def test_half_even_not_half_up(self) -> None:
        # 2.5p → 2p, not 3p — proves policy is half-even
        assert Money.of_pence(5).mul("0.5").pence == 2

    def test_share(self) -> None:
        assert Money.of_pence(100).share(1, 3).pence == 33
        with pytest.raises(ZeroDivisionError):
            Money.of_pence(1).share(1, 0)


class TestDisplay:
    def test_format(self) -> None:
        assert str(Money.of_pence(123456)) == "£1,234.56"
        assert str(Money.of_pence(-500)) == "-£5.00"
        assert str(Money.of_pence(0)) == "£0.00"


class TestPydanticBoundary:
    @pytest.mark.parametrize("raw,expect", [
        (1234, 1234), ("12.34", 1234), ({"pence": 42}, 42),
    ])
    def test_accepted_forms(self, raw, expect: int) -> None:
        assert Payload(amount=raw).amount.pence == expect

    def test_float_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Payload(amount=12.34)

    def test_serialises_as_pence(self) -> None:
        assert Payload(amount=Money.of_pence(50)).model_dump()["amount"] == 50


class TestDates:
    def test_uk_format_roundtrip(self) -> None:
        assert format_uk(parse_uk("05/11/2026")) == "05/11/2026"

    @pytest.mark.parametrize("d,expect", [
        ("2026-02-10", "2026-02-28"),   # non-leap Feb clamps
        ("2024-02-10", "2024-02-29"),   # leap Feb
        ("2026-01-31", "2026-01-31"),
        ("2026-04-15", "2026-04-30"),
    ])
    def test_month_end(self, d: str, expect: str) -> None:
        from datetime import date
        y, m, day = (int(p) for p in d.split("-"))
        assert month_end(date(y, m, day)).isoformat() == expect
