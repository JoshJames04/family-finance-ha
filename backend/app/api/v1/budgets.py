"""Budgets: CRUD, lines, lifecycle transitions, per-line actuals."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    ProfileDep,
    SessionDep,
)
from app.domain import budgets as dom
from app.domain import rbac, schedule
from app.persistence.models import (
    Budget,
    BudgetLine,
    BudgetStatus,
    Category,
    Role,
    UserProfile,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _visible(b: Budget, profile: UserProfile | None) -> bool:
    """Shared budgets (owner NULL) are household-visible; private
    budgets only to their owner or an admin profile (issue #61)."""
    if b.owner_profile_id is None:
        return True
    if profile is None:
        return False
    return (profile.role == Role.admin
            or b.owner_profile_id == profile.id)


def _w(b: Budget, profile: UserProfile | None) -> None:
    """Mutation gate — resolved identities enforced (issue #32)."""
    if not rbac.write_allowed(b.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")


def _get(session: Session, household_id: int, budget_id: int,
         profile: UserProfile | None = None) -> Budget:
    b = session.execute(
        select(Budget).where(Budget.id == budget_id)
        .options(selectinload(Budget.lines))
    ).scalar_one_or_none()
    if (b is None or b.household_id != household_id
            or not _visible(b, profile)):
        raise HTTPException(404, f"budget {budget_id} not found")
    return b


def _detail(session: Session, b: Budget) -> schemas.BudgetDetail:
    return schemas.BudgetDetail(
        **schemas.BudgetOut.model_validate(b).model_dump(),
        lines=[schemas.BudgetLineOut.model_validate(line)
               for line in b.lines],
        read_only=dom.is_read_only(session, b))


@router.get("", response_model=schemas.Page[schemas.BudgetOut])
def list_budgets(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    status: Annotated[BudgetStatus | None, Query()] = None,
    shared_only: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.BudgetOut]:
    q = select(Budget).where(Budget.household_id == household.id)
    if status is not None:
        q = q.where(Budget.status == status)
    if shared_only:
        q = q.where(Budget.owner_profile_id.is_(None))
    rows = [b for b in session.execute(
        q.order_by(Budget.start_date.desc())).scalars().all()
        if _visible(b, profile)]
    return schemas.Page(
        items=[schemas.BudgetOut.model_validate(b) for b in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.BudgetOut, status_code=201)
def create_budget(
    body: schemas.BudgetIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.BudgetOut:
    if not rbac.write_allowed(body.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    b = dom.create_budget(
        session, household.id, body.name, body.period_type,
        body.start_date, body.end_date,
        owner_profile_id=body.owner_profile_id)
    emit(session, household.id, actor, "create", "budget", b.id,
         {"name": b.name, "period_type": b.period_type.value})
    return schemas.BudgetOut.model_validate(b)


@router.get("/{budget_id}", response_model=schemas.BudgetDetail)
def get_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.BudgetDetail:
    return _detail(session, _get(
        session, household.id, budget_id, profile))


@router.post("/{budget_id}/lines",
             response_model=schemas.BudgetLineOut, status_code=201)
def add_line(
    budget_id: int, body: schemas.BudgetLineIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BudgetLineOut:
    b = _get(session, household.id, budget_id, profile)
    _w(b, profile)
    cat = session.get(Category, body.category_id)
    if cat is None or cat.household_id != household.id:
        raise HTTPException(404, "category not found")
    line = dom.add_line(session, b, body.category_id, body.side,
                        body.amount.pence)
    line.rollover = body.rollover
    emit(session, household.id, actor, "create", "budget_line", line.id,
         {"budget_id": b.id, "category_id": line.category_id})
    return schemas.BudgetLineOut.model_validate(line)


@router.patch("/{budget_id}/lines/{line_id}",
              response_model=schemas.BudgetLineOut)
def revise_line(
    budget_id: int, line_id: int, body: schemas.BudgetLineRevise,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BudgetLineOut:
    b = _get(session, household.id, budget_id, profile)
    _w(b, profile)
    line = session.get(BudgetLine, line_id)
    if line is None or line.budget_id != b.id:
        raise HTTPException(404, "line not found")
    line = dom.revise_line(session, line, body.amount.pence,
                           body.reason)
    emit(session, household.id, actor, "update", "budget_line", line.id,
         {"revised_pence": line.revised_pence,
          "reason": body.reason})
    # a revised approved line changes budget-derived rule amounts (#86)
    if b.status is BudgetStatus.approved:
        affected = schedule.rematerialize_budget_derived_rules(
            session, household.id)
        if affected:
            emit(session, household.id, actor, "recompute",
                 "transfer_rule", line.id,
                 {"rule_ids": affected, "line_id": line.id})
    return schemas.BudgetLineOut.model_validate(line)


@router.post("/{budget_id}/approve", response_model=schemas.BudgetOut)
def approve_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> schemas.BudgetOut:
    b = dom.approve(session, _get(session, household.id, budget_id,
                                  profile))
    _w(b, profile)
    emit(session, household.id, actor, "update", "budget", b.id,
         {"status": b.status.value})
    # budget-derived transfer rules recompute on approval (#86)
    affected = schedule.rematerialize_budget_derived_rules(
        session, household.id)
    if affected:
        emit(session, household.id, actor, "recompute",
             "transfer_rule", b.id,
             {"rule_ids": affected, "budget_id": b.id})
    return schemas.BudgetOut.model_validate(b)


@router.post("/{budget_id}/lock", response_model=schemas.BudgetOut)
def lock_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> schemas.BudgetOut:
    b = dom.lock(session, _get(session, household.id, budget_id,
                               profile))
    _w(b, profile)
    emit(session, household.id, actor, "update", "budget", b.id,
         {"status": b.status.value})
    return schemas.BudgetOut.model_validate(b)


@router.get("/{budget_id}/actuals",
            response_model=schemas.BudgetActuals)
def budget_actuals(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.BudgetActuals:
    b = _get(session, household.id, budget_id, profile)
    totals = dom.actuals_for_budget(session, b)
    lines = {line.id: dom.line_analysis(totals[line.id], line)
             for line in b.lines}
    return schemas.BudgetActuals(
        budget_id=b.id, totals=totals, lines=lines)


# ---------- templates + copy-forward (issue #59) ----------------------
from sqlalchemy.orm import selectinload as _sel  # noqa: E402

from app.persistence.models import (  # noqa: E402
    BudgetTemplate,
    BudgetTemplateLine,
)


def _get_template(session: Session, household_id: int,
                  template_id: int) -> BudgetTemplate:
    t = session.execute(
        select(BudgetTemplate).where(BudgetTemplate.id == template_id)
        .options(_sel(BudgetTemplate.lines))
    ).scalar_one_or_none()
    if t is None or t.household_id != household_id:
        raise HTTPException(404, "template not found")
    return t


@router.get("/templates/list",
            response_model=schemas.Page[schemas.BudgetTemplateOut])
def list_templates(
    household: HouseholdDep, session: SessionDep,
) -> schemas.Page[schemas.BudgetTemplateOut]:
    rows = session.execute(
        select(BudgetTemplate).where(
            BudgetTemplate.household_id == household.id)
    ).scalars().all()
    return schemas.Page(
        items=[schemas.BudgetTemplateOut.model_validate(t)
               for t in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("/templates", response_model=schemas.BudgetTemplateOut,
             status_code=201)
def create_template(
    body: schemas.BudgetTemplateIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.BudgetTemplateOut:
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    t = dom.create_template(session, household.id, body.name)
    emit(session, household.id, actor, "create", "budget_template",
         t.id, {"name": t.name})
    return schemas.BudgetTemplateOut.model_validate(t)


@router.get("/templates/{template_id}",
            response_model=schemas.BudgetTemplateDetail)
def get_template(
    template_id: int, household: HouseholdDep, session: SessionDep,
) -> schemas.BudgetTemplateDetail:
    t = _get_template(session, household.id, template_id)
    return schemas.BudgetTemplateDetail(
        **schemas.BudgetTemplateOut.model_validate(t).model_dump(),
        lines=[schemas.BudgetTemplateLineOut.model_validate(x)
               for x in t.lines])


@router.post("/templates/{template_id}/lines",
             response_model=schemas.BudgetTemplateLineOut,
             status_code=201)
def add_template_line(
    template_id: int, body: schemas.BudgetTemplateLineIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BudgetTemplateLineOut:
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    t = _get_template(session, household.id, template_id)
    cat = session.get(Category, body.category_id)
    if cat is None or cat.household_id != household.id:
        raise HTTPException(404, "category not found")
    line = dom.add_template_line(session, t, body.category_id,
                                 body.side, body.amount.pence)
    emit(session, household.id, actor, "create",
         "budget_template_line", line.id,
         {"template_id": t.id})
    return schemas.BudgetTemplateLineOut.model_validate(line)


@router.patch("/templates/{template_id}/lines/{line_id}",
              response_model=schemas.BudgetTemplateLineOut)
def edit_template_line(
    template_id: int, line_id: int,
    body: schemas.BudgetTemplateLinePatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BudgetTemplateLineOut:
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    t = _get_template(session, household.id, template_id)
    line = session.get(BudgetTemplateLine, line_id)
    if line is None or line.template_id != t.id:
        raise HTTPException(404, "template line not found")
    line = dom.update_template_line(session, line,
                                    body.amount.pence)
    emit(session, household.id, actor, "update",
         "budget_template_line", line.id,
         {"amount_pence": line.amount_pence})
    return schemas.BudgetTemplateLineOut.model_validate(line)


@router.post("/{budget_id}/copy-forward",
             response_model=schemas.CopyResult, status_code=201)
def copy_forward(
    budget_id: int, body: schemas.CopyForwardIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.CopyResult:
    src = _get(session, household.id, budget_id, profile)
    _w(src, profile)
    new, warnings = dom.copy_forward(
        session, src, body.name, body.start_date, body.end_date,
        period_type=body.period_type)
    emit(session, household.id, actor, "create", "budget", new.id,
         {"copied_from_id": src.id})
    return schemas.CopyResult(
        budget=schemas.BudgetOut.model_validate(new),
        warnings=warnings)


@router.post("/{budget_id}/apply-template",
             response_model=schemas.ApplyResult)
def apply_template(
    budget_id: int, body: schemas.ApplyTemplateIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.ApplyResult:
    b = _get(session, household.id, budget_id, profile)
    _w(b, profile)
    t = _get_template(session, household.id, body.template_id)
    created, warnings = dom.apply_template(session, b, t)
    emit(session, household.id, actor, "update", "budget", b.id,
         {"from_template_id": t.id, "lines_added": len(created)})
    return schemas.ApplyResult(created=len(created),
                               warnings=warnings)


@router.post("/{budget_id}/rollover",
             response_model=schemas.RolloverResult)
def rollover(
    budget_id: int, body: schemas.RolloverIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.RolloverResult:
    target = _get(session, household.id, budget_id, profile)
    _w(target, profile)
    src = _get(session, household.id, body.source_budget_id,
               profile)
    events = dom.apply_rollover(session, src, target)
    for e in events:
        emit(session, household.id, actor, "rollover", "budget_line",
             int(str(e["line_id"])), e)
    return schemas.RolloverResult(applied=len(events),
                                  carries=events)


@router.post("/{budget_id}/share", response_model=schemas.BudgetOut)
def share_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> schemas.BudgetOut:
    """Explicit private-to-shared conversion - audited (issue #61)."""
    b = _get(session, household.id, budget_id, profile)
    _w(b, profile)
    if (profile is None or
            (b.owner_profile_id is not None
             and profile.role != Role.admin
             and b.owner_profile_id != profile.id)):
        raise HTTPException(403, "only the owner or an admin can share")
    b.owner_profile_id = None
    session.flush()
    emit(session, household.id, actor, "update", "budget", b.id,
         {"shared": True})
    return schemas.BudgetOut.model_validate(b)


@router.get("/{budget_id}/report",
            response_model=schemas.BudgetReport)
def budget_report(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    warn_pct: Annotated[float, Query(ge=0, le=100)] = 10.0,
    over_pct: Annotated[float, Query(ge=0, le=100)] = 20.0,
) -> schemas.BudgetReport:
    b = _get(session, household.id, budget_id, profile)
    rows = dom.report_for_budget(session, b, warn_pct, over_pct)
    return schemas.BudgetReport(
        budget_id=b.id,
        lines=[schemas.BudgetReportLine.model_validate(r)
               for r in rows])


@router.get("/reports/accuracy-ranking",
            response_model=list[schemas.AccuracyRankRow])
def accuracy_ranking(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> list[schemas.AccuracyRankRow]:
    """Worst-forecast categories first, across visible budgets."""
    budgets = session.execute(
        select(Budget).where(Budget.household_id == household.id)
    ).scalars().all()
    visible = [b for b in budgets if _visible(b, profile)]
    return [schemas.AccuracyRankRow.model_validate(r)
            for r in dom.accuracy_ranking(session, visible)]


@router.get("/reports/accuracy/{category_id}",
            response_model=schemas.AccuracyHistory)
def accuracy_history(
    category_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.AccuracyHistory:
    cat = session.get(Category, category_id)
    if cat is None or cat.household_id != household.id:
        raise HTTPException(404, "category not found")
    pts = dom.accuracy_history(session, household.id, category_id)
    # private budgets must not leak into an aggregate for other members
    visible = [
        p for p in pts
        if (b := session.get(Budget, int(str(p["budget_id"]))))
        is not None and _visible(b, profile)]
    return schemas.AccuracyHistory(
        category_id=category_id,
        points=[schemas.AccuracyPoint.model_validate(p)
                for p in visible])


@router.patch("/{budget_id}/lines",
              response_model=schemas.BulkReviseResult)
def bulk_revise_lines(
    budget_id: int, body: schemas.BulkLineRevise,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BulkReviseResult:
    """Table-UX bulk edit: one request revises many lines. Invalid
    transitions bubble as 409 problem-details like single edits."""
    b = _get(session, household.id, budget_id, profile)
    _w(b, profile)
    updated, skipped = 0, []
    for item in body.revisions:
        line = session.get(BudgetLine, item.line_id)
        if line is None or line.budget_id != b.id:
            skipped.append(item.line_id)
            continue
        dom.revise_line(session, line, item.amount.pence,
                        item.reason)
        updated += 1
    emit(session, household.id, actor, "update", "budget", b.id,
         {"bulk_revisions": updated, "skipped": skipped})
    return schemas.BulkReviseResult(updated=updated,
                                    skipped=skipped)
