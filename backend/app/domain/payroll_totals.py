"""Payroll totals without assuming imported component lists are complete."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.persistence.models import PayrollComponentKind, PayrollRunStatus

EARNINGS = {
    PayrollComponentKind.base_salary,
    PayrollComponentKind.overtime,
    PayrollComponentKind.bonus,
    PayrollComponentKind.allowance,
}


@dataclass(frozen=True)
class PayrollTotals:
    forecast_gross_pence: int
    forecast_net_pence: int
    actual_gross_pence: int | None
    actual_net_pence: int | None


def signed_component(kind: PayrollComponentKind, value: int, *, native: bool = False) -> int:
    """Older imports contain positive deductions; native runs use negatives."""
    return value if native or kind in EARNINGS else -abs(value)


class ComponentValues(Protocol):
    @property
    def kind(self) -> PayrollComponentKind: ...
    @property
    def forecast_pence(self) -> int: ...
    @property
    def actual_pence(self) -> int | None: ...


class RunValues(Protocol):
    @property
    def status(self) -> PayrollRunStatus: ...
    @property
    def rule_set_version(self) -> int: ...
    @property
    def gross_pence(self) -> int: ...
    @property
    def net_pence(self) -> int: ...
    @property
    def verified_gross_pence(self) -> int | None: ...
    @property
    def verified_net_pence(self) -> int | None: ...
    @property
    def components(self) -> Sequence[ComponentValues]: ...


def totals_for_run(run: RunValues) -> PayrollTotals:
    actual_gross: int | None = None
    actual_net: int | None = None
    if run.status is PayrollRunStatus.reconciled or any(
        component.actual_pence is not None for component in run.components
    ):
        actual_gross = run.gross_pence
        actual_net = run.net_pence
        explicit_signs = getattr(run, "signed_components", None)
        native = explicit_signs if explicit_signs is not None else (run.rule_set_version or 0) > 0
        for component in run.components:
            if component.actual_pence is None:
                continue
            delta = signed_component(
                component.kind, component.actual_pence, native=native
            ) - signed_component(component.kind, component.forecast_pence, native=native)
            actual_net += delta
            if component.kind in EARNINGS:
                actual_gross += delta
        if getattr(run, "verified_gross_pence", None) is not None:
            actual_gross = run.verified_gross_pence
        if getattr(run, "verified_net_pence", None) is not None:
            actual_net = run.verified_net_pence
    return PayrollTotals(run.gross_pence, run.net_pence, actual_gross, actual_net)
