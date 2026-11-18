"""Reject unverified imported YTD history without changing financial records."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain import payroll, payrun
from app.domain.tax import tax_year_for
from app.persistence.models import (
    PayrollComponent,
    PayrollComponentKind,
    PayrollRun,
    PayrollRunStatus,
)
from tests import test_bonus_api as api_fixtures
from tests import test_employer_bonuses as bonus_fixtures

api = api_fixtures.api
employer = bonus_fixtures.employer


@pytest.mark.parametrize("missing_base", [True, False])
@pytest.mark.parametrize("year_delimiter", ["-", "/"])
def test_bonus_rejects_unverified_prior_history_and_rolls_back_every_change(
    api,
    missing_base,
    year_delimiter,
):
    client, headers, path, _ = api
    employment_id = int(path.rsplit("/", 1)[1])
    with Session(client.app.state.engine) as db:
        prior = PayrollRun(
            employment_id=employment_id,
            period_start=date(2025, 4, 1),
            period_end=date(2025, 4, 30),
            pay_date=date(2025, 4, 28),
            status=PayrollRunStatus.reconciled,
            tax_year=tax_year_for(date(2025, 4, 28)).replace("-", year_delimiter),
            rule_set_version=1,
            gross_pence=300000,
            net_pence=240000,
        )
        # The retired importer stored positive deductions and no salary baseline,
        # even with rule_set_version=1. A partial native baseline is unsafe too.
        prior.components = [
            PayrollComponent(
                kind=PayrollComponentKind.overtime
                if missing_base
                else PayrollComponentKind.base_salary,
                label="Imported earnings",
                forecast_pence=10000,
                actual_pence=10000,
            ),
            PayrollComponent(
                kind=PayrollComponentKind.tax,
                label="PAYE",
                forecast_pence=20000,
                actual_pence=20000,
            ),
            PayrollComponent(
                kind=PayrollComponentKind.ni, label="NI", forecast_pence=40000, actual_pence=40000
            ),
        ]
        db.add(prior)
        db.commit()
    before = client.get(path + "/payroll-runs", headers=headers).json()
    result = client.post(
        path + "/bonuses",
        headers=headers,
        json={
            "pay_month": "2025-05-01",
            "manual_amount": "1000",
        },
    )
    assert result.status_code == 409, result.text
    assert "verified" in result.text.lower() and (
        "year-to-date" in result.text.lower() or "ytd" in result.text.lower()
    )
    assert client.get(path + "/bonuses", headers=headers).json() == []
    assert client.get(path + "/payroll-runs", headers=headers).json() == before


def test_missing_payslip_values_cannot_become_verified_history(session, employer):
    from app.domain.payroll import PayrollError

    april = payrun.generate(
        session, employer, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    with pytest.raises(PayrollError, match="payslip gross and net"):
        payrun.reconcile(session, april, {})
    assert april.status is PayrollRunStatus.forecast


@pytest.mark.parametrize(
    "code", ["1257L M1", "1257L W1", "BR M1", "D0 M1", "D1 M1", "NT M1", "gross_only"]
)
def test_non_cumulative_bonus_forecast_ignores_unusable_prior_history(session, employer, code):
    if code == "gross_only":
        employer.deduction_mode = "gross_only"
    else:
        payroll.add_tax_code_revision(session, employer, date(2025, 5, 1), code)
    row = PayrollRun(
        employment_id=employer.id,
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
        status=PayrollRunStatus.reconciled,
        tax_year="2025/26",
        rule_set_version=1,
        gross_pence=300000,
        net_pence=240000,
        components=[
            PayrollComponent(
                kind=PayrollComponentKind.tax,
                label="Legacy PAYE",
                forecast_pence=20000,
                actual_pence=20000,
            )
        ],
    )
    session.add(row)
    session.flush()
    run = payrun.generate(
        session,
        employer,
        start=date(2025, 5, 1),
        end=date(2025, 5, 31),
        pay_date=date(2025, 5, 28),
        bonus_pence=100000,
    )
    assert run.status is PayrollRunStatus.forecast
    assert run.gross_pence > 100000
    assert sum(c.forecast_pence for c in run.components) == run.net_pence
