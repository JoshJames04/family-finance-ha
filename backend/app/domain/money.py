"""Money — the only legal currency representation (ADR-0003).

Internally integer minor units (pence). Multiplication/division by rates
or multipliers goes through Decimal and rounds half-even at the penny —
the single rounding policy for the whole codebase, pinned by tests.

Pydantic boundary rules (strict):
  - int           → pence
  - "12.34" / str → pounds, must be well-formed (max 2dp)
  - {"pence": n}  → explicit dict form
  - Money         → passthrough
  - float         → REJECTED (this is a correctness gate, not a nicety)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from functools import total_ordering
from typing import Any, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

_CENT = Decimal("0.01")


@total_ordering
@dataclass(frozen=True)
class Money:
    pence: int
    currency: str = "GBP"

    def __post_init__(self) -> None:
        if self.currency != "GBP":
            raise ValueError("only GBP is supported")
        if not isinstance(self.pence, int) or isinstance(self.pence, bool):
            raise TypeError("pence must be an int")

    # ---- constructors -------------------------------------------------
    @classmethod
    def of_pence(cls, pence: int) -> Self:
        return cls(pence=pence)

    @classmethod
    def of_pounds(cls, value: int | str | Decimal) -> Self:
        dec = Decimal(str(value))
        return cls(pence=int((dec * 100).quantize(Decimal("1"),
                                                rounding=ROUND_HALF_EVEN)))

    # ---- arithmetic ---------------------------------------------------
    def __add__(self, other: Self) -> Self:
        return type(self)(self.pence + other.pence)

    def __sub__(self, other: Self) -> Self:
        return type(self)(self.pence - other.pence)

    def __neg__(self) -> Self:
        return type(self)(-self.pence)

    def __lt__(self, other: Self) -> bool:
        return self.pence < other.pence

    def mul(self, factor: int | str | Decimal) -> Self:
        """Multiply by a rate/multiplier; half-even rounding at the penny."""
        dec = Decimal(str(factor)) * self.pence
        return type(self)(int(dec.quantize(Decimal("1"),
                                           rounding=ROUND_HALF_EVEN)))

    def share(self, numerator: int, denominator: int) -> Self:
        """Proportional split, e.g. share(1, 3) → a third, pence-exact."""
        if denominator == 0:
            raise ZeroDivisionError("share denominator")
        dec = Decimal(self.pence) * numerator / denominator
        return type(self)(int(dec.quantize(Decimal("1"),
                                           rounding=ROUND_HALF_EVEN)))

    # ---- display ------------------------------------------------------
    def pounds_decimal(self) -> Decimal:
        return (Decimal(self.pence) / 100).quantize(_CENT)

    def format_gbp(self) -> str:
        sign = "-" if self.pence < 0 else ""
        q = abs(self.pounds_decimal())
        return f"{sign}£{q:,.2f}"

    def __str__(self) -> str:
        return self.format_gbp()

    # ---- pydantic -----------------------------------------------------
    @classmethod
    def _validate(cls, value: Any) -> Self:
        # ValueError (not TypeError) so pydantic surfaces ValidationError
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise ValueError("bool is not money")
        if isinstance(value, float):
            raise ValueError(
                "float is rejected for currency — use int pence or a "
                "2dp string"
            )
        if isinstance(value, int):
            return cls.of_pence(value)
        if isinstance(value, Decimal):
            return cls.of_pounds(value)
        if isinstance(value, str):
            return cls.of_pounds(value)
        if isinstance(value, dict) and "pence" in value:
            return cls.of_pence(int(value["pence"]))
        raise ValueError(f"cannot parse Money from {type(value).__name__}")

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: type, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda m: m.pence
            ),
        )


ZERO = Money.of_pence(0)
