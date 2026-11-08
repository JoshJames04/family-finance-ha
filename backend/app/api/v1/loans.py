"""Loans: records, derived outstanding, repayments, drift (#93)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import loans
from app.persistence.models import Loan, Repayment, Transaction

router = APIRouter(prefix="/loans", tags=["loans"])


def _get(session: Session, household_id: int, lid: int) -> Loan:
    ln = session.get(Loan, lid)
    if ln is None or ln.household_id != household_id:
        raise HTTPException(404, f"loan {lid} not found")
    return ln


def _position(session: Session, ln: Loan) -> schemas.LoanPositionOut:
    d = loans.schedule_drift(session, ln)
    return schemas.LoanPositionOut(
        outstanding_pence=loans.outstanding(session, ln),
        repaid_pence=loans.repaid_pence(session, ln),
        accrued_interest_pence=loans.accrued_interest(
            ln, date.today()),
        expected_to_date_pence=d.expected_pence,
        drift_pence=d.drift_pence)


@router.get("", response_model=schemas.Page[schemas.LoanOut])
def list_loans(
    household: HouseholdDep, session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.LoanOut]:
    stmt = select(Loan).where(Loan.household_id == household.id)
    if not include_inactive:
        stmt = stmt.where(Loan.active.is_(True))
    rows = list(session.execute(stmt).scalars().all())
    return schemas.Page(
        items=[schemas.LoanOut.model_validate(ln) for ln in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.LoanOut, status_code=201)
def create_loan(
    body: schemas.LoanIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.LoanOut:
    ln = Loan(
        household_id=household.id, party=body.party,
        direction=body.direction,
        principal_pence=body.principal.pence,
        start_date=body.start_date,
        expected_payment_pence=body.expected_payment.pence
        if body.expected_payment else None,
        interest_bps_pa=body.interest_bps_pa, notes=body.notes,
        owner_profile_id=body.owner_profile_id,
        active=True)
    session.add(ln)
    session.flush()
    emit(session, household.id, actor, "create", "loan", ln.id,
         {"party": ln.party, "direction": ln.direction.value})
    return schemas.LoanOut.model_validate(ln)


@router.get("/{lid}", response_model=schemas.LoanPositionOut)
def loan_position(
    lid: int, household: HouseholdDep, session: SessionDep,
) -> schemas.LoanPositionOut:
    """Derived position — outstanding is always computed (#93)."""
    return _position(session, _get(session, household.id, lid))


@router.post("/{lid}/repayments",
             response_model=schemas.RepaymentOut, status_code=201)
def add_repayment(
    lid: int, body: schemas.RepaymentIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.RepaymentOut:
    ln = _get(session, household.id, lid)
    rp = Repayment(loan_id=ln.id, date=body.date,
                   amount_pence=body.amount.pence, notes=body.notes)
    if body.transaction_id is not None:
        tx = session.get(Transaction, body.transaction_id)
        if tx is None:
            raise HTTPException(
                404, f"transaction {body.transaction_id} not found")
        if tx.account.household_id != ln.household_id:
            raise HTTPException(409, "transaction belongs to another "
                                     "household")
        rp.transaction_id = tx.id
    session.add(rp)
    session.flush()
    emit(session, household.id, actor, "create", "repayment", rp.id,
         {"loan_id": ln.id})
    return schemas.RepaymentOut.model_validate(rp)


@router.get("/{lid}/repayments",
            response_model=list[schemas.RepaymentOut])
def list_repayments(
    lid: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.RepaymentOut]:
    """History with running outstanding after each entry (#94)."""
    ln = _get(session, household.id, lid)
    out = []
    for r, bal in loans.repayment_balances(session, ln):
        row = schemas.RepaymentOut.model_validate(r)
        row.balance_after_pence = bal
        out.append(row)
    return out


@router.get("/{lid}/projection",
            response_model=schemas.LoanProjectionOut)
def loan_projection(
    lid: int, household: HouseholdDep, session: SessionDep,
    extra_monthly: Annotated[int, Query(ge=0)] = 0,
) -> schemas.LoanProjectionOut:
    """What-if: debt-free date at expected rate vs rate + extra (#94)."""
    ln = _get(session, household.id, lid)
    base = loans.projected_payoff(session, ln)
    extra = loans.projected_payoff(
        session, ln, extra_monthly_pence=extra_monthly)
    saved = (base.months - extra.months
             if base.months is not None and extra.months is not None
             else None)
    return schemas.LoanProjectionOut(
        baseline=schemas.PayoffPoint(
            months=base.months, payoff_date=base.payoff_date),
        with_extra=schemas.PayoffPoint(
            months=extra.months, payoff_date=extra.payoff_date),
        extra_monthly_pence=extra_monthly, months_saved=saved)


@router.delete("/{lid}/repayments/{rid}", status_code=204)
def delete_repayment(
    lid: int, rid: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    ln = _get(session, household.id, lid)
    rp = session.get(Repayment, rid)
    if rp is None or rp.loan_id != ln.id:
        raise HTTPException(404, f"repayment {rid} not found")
    session.delete(rp)
    session.flush()
    emit(session, household.id, actor, "delete", "repayment", rid,
         {"loan_id": ln.id})


@router.delete("/{lid}", status_code=204)
def deactivate_loan(
    lid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    ln = _get(session, household.id, lid)
    ln.active = False
    emit(session, household.id, actor, "deactivate", "loan", ln.id)
