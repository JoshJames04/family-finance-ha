"""Complete synthetic payslip facts for tests of lifecycle, not tax formulas."""

from app.domain import payrun
from app.persistence.models import PayrollComponentKind as K


def confirmation_payload(run, changes=None):
    components = run["components"] if isinstance(run, dict) else run.components
    values = {kind: 0 for kind in K}
    for c in components:
        kind = c["kind"] if isinstance(c, dict) else c.kind
        amount = c["forecast_pence"] if isinstance(c, dict) else c.forecast_pence
        values[kind] = values.get(kind, 0) + amount
    values.setdefault(K.base_salary, 0)
    values.update(changes or {})
    gross = sum(
        v for k, v in values.items() if k in {K.base_salary, K.overtime, K.bonus, K.allowance}
    )
    gross += values.get(K.unpaid_leave, 0)
    return dict(
        actuals=values,
        payslip_gross_pence=gross,
        payslip_net_pence=sum(values.values()),
        taxable_pence=gross,
    )


def confirm(session, run, changes=None):
    return payrun.reconcile(session, run, **confirmation_payload(run, changes))
