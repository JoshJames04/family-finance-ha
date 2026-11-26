"""Expected movements: the dated forecast stream + manual items."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.api.v1.budgets import _get as get_budget
from app.domain import budget_periods, contributions, rbac, schedule
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    Role,
    Transaction,
    TransferRule,
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


def _movement_out(session: Session, movement: ExpectedMovement) -> schemas.ExpectedMovementOut:
    result = schemas.ExpectedMovementOut.model_validate(movement)
    result.outstanding_pence = contributions.status(session, movement).outstanding_pence
    return result


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
               and (budget is None
                    or a.owner_profile_id == budget.owner_profile_id)}
    return [_movement_out(session, m)
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
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
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
    return _movement_out(session, m)


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
    profile: ProfileDep,
) -> dict[str, int]:
    """Bulk regenerate future rows from all sources (idempotent)."""
    if not rbac.write_allowed(None, profile) or (
            profile is not None and profile.role != Role.admin):
        raise HTTPException(403, "household rematerialisation requires admin")
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
    profile: ProfileDep,
    window_days: Annotated[int, Query(ge=0, le=62)] =
    contributions.DEFAULT_WINDOW_DAYS,
) -> schemas.MatchResult:
    """Auto-match real transfer legs to expected movements (#87)."""
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    n = contributions.auto_match(session, household.id,
                                 window_days=window_days,
                                 account_ids=(_visible_accounts(session, household.id, profile)
                                              if profile is not None else None))
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
    if tx.account_id != mv.account_id and profile is not None:
        rule = session.get(TransferRule, mv.source_id)
        source_id = (mv.transfer_source_account_id or rule.source_account_id) if rule else None
        owner_account = session.get(Account, source_id) if source_id is not None else None
        if (rule is None or rule.household_id != household.id or owner_account is None
                or not rbac.write_allowed(owner_account.owner_profile_id, profile)):
            raise HTTPException(403, "only the contributor or an admin can confirm this transfer")
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


@router.get("/contributions/cycles", response_model=list[schemas.SharedContributionCycle])
def contribution_cycles(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    start: Annotated[date, Query()], end: Annotated[date, Query()],
    account_id: int | None = None,
) -> list[schemas.SharedContributionCycle]:
    """Show each member's contribution cycle and next instalment without moving the plan."""
    today = date.today()
    current = start <= today <= end
    members = session.scalars(select(UserProfile).where(
        UserProfile.household_id == household.id, UserProfile.is_active.is_(True))).all()
    windows = {}
    for member in members:
        window = budget_periods.resolve_member_period(
            session, household.id, member.id, start, end)
        month_end = end
        # An early payday on the 1st may open the next labelled cycle
        # on the last working day of the current calendar month.
        while current and window.basis == "pay_cycle" and window.end < today:
            month_start = month_end + timedelta(days=1)
            month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
            window = budget_periods.resolve_member_period(
                session, household.id, member.id, month_start, month_end)
        windows[member.id] = window
    if not windows:
        return []

    accounts = {a.id: a for a in session.scalars(select(Account).where(
        Account.household_id == household.id, Account.archived.is_(False)))}
    destinations = {a.id for a in accounts.values()
                    if a.owner_profile_id is None and rbac.visible(None, profile)
                    and (account_id is None or account_id == a.id)}
    rules = {
        rule.id: rule
        for rule in session.scalars(select(TransferRule).where(
            TransferRule.household_id == household.id,
            TransferRule.source_account_id.in_(accounts),
            TransferRule.dest_account_id.in_(accounts),
        ))
    }
    scheduled = [
        movement for movement in schedule.stream(
            session, household.id, start=min(w.start for w in windows.values()),
            end=max(max(w.end for w in windows.values()),
                    today + timedelta(days=90) if current else end))
        if movement.source_type == ExpectedSource.transfer_rule
        and movement.dismissed_at is None and movement.source_id in rules
    ]

    def occurrence(movement: ExpectedMovement) -> tuple[int, date, int, int]:
        rule = rules[movement.source_id]
        return (rule.id, movement.expected_date,
                movement.transfer_source_account_id or rule.source_account_id,
                movement.transfer_dest_account_id or rule.dest_account_id)

    by_occurrence = {}
    rule_members = {}
    for m in scheduled:
        if m.amount_pence <= 0:
            continue
        key = occurrence(m)
        source = accounts.get(key[2])
        if (m.account_id not in destinations or key[3] != m.account_id
                or source is None or source.owner_profile_id not in windows):
            continue
        by_occurrence[key] = m
        rule_members[m.id] = source.owner_profile_id
    movements = list(by_occurrence.values())
    outgoing = {
        m.id: receipt
        for m in scheduled if m.amount_pence < 0
        if (receipt := by_occurrence.get(occurrence(m))) is not None
    }
    matched = contributions.shared_payment_totals(session, movements, outgoing)
    rows: dict[int, list[schemas.SharedContributionPayment]] = {
        member.id: [] for member in members
    }
    for m in movements:
        member_id = rule_members[m.id]
        assert m.account_id is not None and member_id is not None
        rows[member_id].append(schemas.SharedContributionPayment(
            movement_id=m.id, account_id=m.account_id, due_date=m.expected_date,
            expected_pence=m.amount_pence, matched_pence=matched[m.id],
        ))
    return [schemas.SharedContributionCycle(
        profile_id=member_id, start=window.start, end=window.end,
        basis="pay_cycle" if window.basis == "pay_cycle" else "calendar",
        warning=window.warning,
        payments=[p for p in rows[member_id] if window.start <= p.due_date <= window.end],
        next_payment=next((p for p in rows[member_id] if p.due_date > today or
                           (p.due_date == today and p.matched_pence < p.expected_pence)), None)
        if current else None,
    ) for member_id, window in windows.items()]


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
