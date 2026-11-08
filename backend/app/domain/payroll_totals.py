"""Payroll totals without assuming imported component lists are complete."""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.models import PayrollComponentKind, PayrollRun, PayrollRunStatus

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


def signed_component(kind: PayrollComponentKind, value: int) -> int:
    """Older imports contain positive deductions; native runs use negatives."""
    return value if kind in EARNINGS else -abs(value)


def totals_for_run(run: PayrollRun) -> PayrollTotals:
    actual_gross: int | None = None
    actual_net: int | None = None
    if run.status is PayrollRunStatus.reconciled:
        actual_gross = run.gross_pence
        actual_net = run.net_pence
        for component in run.components:
            if component.actual_pence is None:
                continue
            delta = (
                signed_component(component.kind, component.actual_pence)
                - signed_component(component.kind, component.forecast_pence)
            )
            actual_net += delta
            if component.kind in EARNINGS:
                actual_gross += delta
    return PayrollTotals(
        run.gross_pence, run.net_pence, actual_gross, actual_net)
