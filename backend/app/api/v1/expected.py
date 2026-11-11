"""Expected movements: the dated forecast stream + manual items."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.api.v1.budgets import _get as get_budget
from app.domain import contributions, rbac, schedule
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
    UserProfile,
)

router = APIRouter(prefix="/expected-movements",
                   tags=["expected-movements"])


def _visible_accounts(session: Session, household_id: int,
                      profile: UserProfile | None) -> set[int]:
    return {a.id for a in session.scalars(select(Account).where(
        Account.household_id == household_id))
        if rbac.visible(a.owner_profile_id, profile)}


def _visible_movement(movement: ExpectedMovement,
                      allowed: set[int]) -> bool:
    # Accountless manual plans and obligations are household resources.
    # Legacy salary rows without a destination have no safe owner boundary.
    return (movement.account_id in allowed
            or (movement.account_id is None and movement.source_type not in (
                ExpectedSource.employment, ExpectedSource.payroll_run)))


@router.get("", response_model=list[schemas.ExpectedMovementOut])
def list_movements(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    account_id: int | None = None,
    budget_id: int | None = None,
) -> list[schemas.ExpectedMovementOut]:
    budget = (get_budget(session, household.id, budget_id, profile)
              if budget_id is not None else None)
    accounts = session.scalars(select(Account).where(
        Account.household_id == household.id)).all()
    allowed = {a.id for a in accounts
               if rbac.visible(a.owner_profile_id, profile)
               and (budget is None or budget.owner_profile_id is None
                    or a.owner_profile_id == budget.owner_profile_id)}
    return [schemas.ExpectedMovementOut.model_validate(m)
            for m in schedule.stream(
                session, household.id, start=start, end=end,
                account_id=account_id)
            if _visible_movement(m, allowed)
            and (budget is None or budget.owner_profile_id is None
                 or m.account_id in allowed)]


@router.post("", response_model=schemas.ExpectedMovementOut,
             status_code=201)
def create_manual(
    body: schemas.ExpectedMovementIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.ExpectedMovementOut:
    if body.account_id is not None:
        account = session.get(Account, body.account_id)
        if account is None or account.household_id != household.id:
            raise HTTPException(404, "account not found")
        if not rbac.write_allowed(account.owner_profile_id, profile):
            raise HTTPException(404, "account not found")
    m = schedule.add_manual(
        session, household.id, account_id=body.account_id,
        expected_date=body.expected_date,
        amount_pence=body.amount.pence, label=body.label)
    emit(session, household.id, actor, "create", "expected_movement",
         m.id, {"expected_date": str(m.expected_date)})
    return schemas.ExpectedMovementOut.model_validate(m)


@router.delete("/{mid}", status_code=204)
def delete_manual(
    mid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> None:
    m = _movement_or_404(session, household.id, mid, profile)
    if m.source_type is not ExpectedSource.manual:
        raise HTTPException(
            409, "only manual expected movements can be deleted — "
            "sourced rows regenerate from their source")
    session.execute(delete(ExpectedMovement).where(
        ExpectedMovement.id == mid))
    emit(session, household.id, actor, "delete", "expected_movement",
         mid, {})


@router.post("/materialise", status_code=202)
def rematerialise(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> dict[str, int]:
    """Bulk regenerate future rows from all sources (idempotent)."""
    n = schedule.materialize_all(session, household)
    emit(session, household.id, actor, "materialise",
         "expected_movement", household.id, {"count": n})
    return {"materialised": n}


@router.get("/contributions",
            response_model=list[schemas.ContributionStatusOut])
def contribution_status(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    start: Annotated[date, Query()], end: Annotated[date, Query()],
) -> list[schemas.ContributionStatusOut]:
    """Satisfied/partial/overdue state per expected transfer (#87)."""
    allowed = _visible_accounts(session, household.id, profile)
    return [schemas.ContributionStatusOut(
        movement_id=s.movement_id, expected_pence=s.expected_pence,
        matched_pence=s.matched_pence,
        outstanding_pence=s.outstanding_pence, state=s.state.value)
        for s in contributions.contributions(
            session, household.id, start=start, end=end)
        if (m := session.get(ExpectedMovement, s.movement_id)) is not None
        and _visible_movement(m, allowed)]


@router.post("/match", response_model=schemas.MatchResult)
def auto_match_contributions(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    window_days: Annotated[int, Query(ge=0, le=62)] =
    contributions.DEFAULT_WINDOW_DAYS,
) -> schemas.MatchResult:
    """Auto-match real transfer legs to expected movements (#87)."""
    n = contributions.auto_match(session, household.id,
                                 window_days=window_days)
    emit(session, household.id, actor, "match", "expected_movement",
         household.id, {"matched": n, "window_days": window_days})
    return schemas.MatchResult(matched=n)


@router.post("/{mid}/matches", status_code=201,
             response_model=schemas.ContributionStatusOut)
def confirm_match(
    mid: int, body: schemas.ContributionMatchIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.ContributionStatusOut:
    """Manual confirmation of a leg against an expected movement."""
    mv = _movement_or_404(session, household.id, mid, profile)
    tx = session.get(Transaction, body.transaction_id)
    if tx is None:
        raise HTTPException(
            404, f"transaction {body.transaction_id} not found")
    account = session.get(Account, tx.account_id)
    if (account is None or account.household_id != household.id
            or not rbac.write_allowed(account.owner_profile_id, profile)):
        raise HTTPException(404, "transaction not found")
    contributions.confirm(
        session, mv, tx,
        pence=body.amount.pence if body.amount else None)
    emit(session, household.id, actor, "confirm", "expected_movement",
         mv.id, {"transaction_id": tx.id})
    s = contributions.status(session, mv)
    return schemas.ContributionStatusOut(
        movement_id=s.movement_id, expected_pence=s.expected_pence,
        matched_pence=s.matched_pence,
        outstanding_pence=s.outstanding_pence, state=s.state.value)


@router.get("/outstanding",
            response_model=list[schemas.OutstandingItemOut])
def outstanding_list(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.OutstandingItemOut]:
    """Past-due unmatched obligations with age in days (#88)."""
    allowed = _visible_accounts(session, household.id, profile)
    return [schemas.OutstandingItemOut(
        movement_id=i.movement_id, account_id=i.account_id,
        label=i.label, due_date=i.due_date, age_days=i.age_days,
        outstanding_pence=i.outstanding_pence)
        for i in contributions.outstanding(session, household.id)
        if (m := session.get(ExpectedMovement, i.movement_id)) is not None
        and _visible_movement(m, allowed)]


@router.get("/contributions/by-member",
            response_model=list[schemas.MemberContributionOut])
def contributions_by_member(
    household: HouseholdDep, session: SessionDep,
    start: Annotated[date, Query()], end: Annotated[date, Query()],
) -> list[schemas.MemberContributionOut]:
    """Per-member matched-transfer totals for the period (#89).

    Totals equal real matched transfers — never scheduled amounts.
    Private transfers (same owner both sides) never appear."""
    return [schemas.MemberContributionOut(
        profile_id=c.profile_id, display_name=c.display_name,
        contributed_pence=c.contributed_pence)
        for c in contributions.member_contributions(
            session, household.id, start=start, end=end)]


@router.get("/requirements",
            response_model=list[schemas.RequirementOut])
def requirement_totals(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    start: Annotated[date, Query()], end: Annotated[date, Query()],
) -> list[schemas.RequirementOut]:
    """Per-account requirement totals — scheduled minus satisfied
    (#88: "the bills account needs £X by dd/MM")."""
    allowed = _visible_accounts(session, household.id, profile)
    return [schemas.RequirementOut(
        account_id=r.account_id, due_date=r.due_date,
        scheduled_pence=r.scheduled_pence,
        satisfied_pence=r.satisfied_pence,
        outstanding_pence=r.outstanding_pence)
        for r in contributions.requirement_totals(
            session, household.id, start=start, end=end)
        if r.account_id in allowed]


def _movement_or_404(session: Session, household_id: int,
                     mid: int, profile: UserProfile | None) -> ExpectedMovement:
    mv: ExpectedMovement | None = session.get(ExpectedMovement, mid)
    if mv is None or mv.household_id != household_id:
        raise HTTPException(404, f"expected movement {mid} not found")
    if profile is not None:
        allowed = _visible_accounts(session, household_id, profile)
        if not _visible_movement(mv, allowed):
            raise HTTPException(404, f"expected movement {mid} not found")
        account = session.get(Account, mv.account_id) if mv.account_id else None
        if not rbac.write_allowed(account.owner_profile_id if account else None, profile):
            raise HTTPException(403, "role cannot mutate this scope")
    return mv


@router.post("/{mid}/write-off", status_code=200)
def write_off_movement(
    mid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> dict[str, int]:
    """Abandon an obligation — audited; excluded from outstanding and
    requirement totals thereafter (#88)."""
    mv = _movement_or_404(session, household.id, mid, profile)
    contributions.write_off(session, mv, when=datetime.now())
    emit(session, household.id, actor, "write_off",
         "expected_movement", mv.id, {"label": mv.label})
    return {"written_off": mv.id}


@router.post("/{mid}/reschedule", status_code=200)
def reschedule_movement(
    mid: int, body: schemas.RescheduleIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> dict[str, str]:
    """Push a due date forward for an obligation still to be honoured
    — audited (#88)."""
    mv = _movement_or_404(session, household.id, mid, profile)
    old = mv.expected_date
    contributions.reschedule(session, mv, body.due_date)
    emit(session, household.id, actor, "reschedule",
         "expected_movement", mv.id,
         {"from": str(old), "to": str(body.due_date)})
    return {"due_date": str(body.due_date)}
