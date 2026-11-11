"""Auto-categorisation rules (issue #68): rule CRUD, apply pass,
transfer/refund auto-matching."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import categorise
from app.persistence.models import (
    Account,
    CategorisationRule,
    Category,
    MatchType,
    ProviderCategoryMap,
    RuleSource,
    Transaction,
)

router = APIRouter(prefix="/categorisation",
                   tags=["categorisation"])


def _get(session: Session, household_id: int, rule_id: int
         ) -> CategorisationRule:
    r = session.get(CategorisationRule, rule_id)
    if r is None or r.household_id != household_id:
        raise HTTPException(404, f"rule {rule_id} not found")
    return r


def _category(session: Session, household_id: int,
              category_id: int) -> None:
    c = session.get(Category, category_id)
    if c is None or c.household_id != household_id:
        raise HTTPException(422, f"category {category_id} not found")


@router.get("/rules",
            response_model=list[schemas.CategorisationRuleOut])
def list_rules(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.CategorisationRuleOut]:
    rows = session.scalars(
        select(CategorisationRule)
        .where(CategorisationRule.household_id == household.id)
        .order_by(CategorisationRule.priority, CategorisationRule.id))
    return [schemas.CategorisationRuleOut.model_validate(r)
            for r in rows]


@router.post("/rules", response_model=schemas.CategorisationRuleOut,
             status_code=201)
def create_rule(
    body: schemas.CategorisationRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.CategorisationRuleOut:
    _category(session, household.id, body.category_id)
    r = CategorisationRule(
        household_id=household.id, pattern=body.pattern,
        match_type=MatchType(body.match_type),
        category_id=body.category_id, priority=body.priority,
        auto_apply=body.auto_apply, source=RuleSource.manual)
    session.add(r)
    session.flush()
    emit(session, household.id, actor, "create",
         "categorisation_rule", r.id, {"pattern": body.pattern})
    return schemas.CategorisationRuleOut.model_validate(r)


@router.patch("/rules/{rule_id}",
              response_model=schemas.CategorisationRuleOut)
def patch_rule(
    rule_id: int, body: dict[str, object], household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.CategorisationRuleOut:
    r = _get(session, household.id, rule_id)
    for k in ("pattern", "priority", "auto_apply", "enabled",
              "category_id"):
        if k in body:
            if k == "category_id":
                _category(session, household.id, int(str(body[k])))
            setattr(r, k, body[k])
    session.flush()
    emit(session, household.id, actor, "update",
         "categorisation_rule", r.id, dict(body))
    return schemas.CategorisationRuleOut.model_validate(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    r = _get(session, household.id, rule_id)
    emit(session, household.id, actor, "delete",
         "categorisation_rule", r.id)
    session.delete(r)


@router.post("/apply", response_model=schemas.CategoriseApplyOut)
def apply(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    since: Annotated[date | None, Query()] = None,
) -> schemas.CategoriseApplyOut:
    """Run the rules over uncategorised transactions — suggestions
    flagged, auto_apply rules applied."""
    r = categorise.apply_rules(session, household.id, since)
    emit(session, household.id, actor, "apply",
         "categorisation_rule", 0,
         {"applied": r.auto_applied, "suggested": r.suggested})
    return schemas.CategoriseApplyOut(
        auto_applied=r.auto_applied, suggested=r.suggested,
        unmatched=r.unmatched)


@router.post("/auto-pair", response_model=schemas.PairRefundOut)
def auto_pair(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    since: Annotated[date | None, Query()] = None,
) -> schemas.PairRefundOut:
    """Identify internal-account movements → Transfer objects."""
    n = categorise.auto_pair_transfers(session, household.id, since)
    emit(session, household.id, actor, "auto_pair",
         "transfer", 0, {"pairs": n})
    return schemas.PairRefundOut(linked=n)


@router.get("/provider-categories",
            response_model=list[schemas.ObservedProviderCategoryOut])
def list_provider_categories(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.ObservedProviderCategoryOut]:
    """Every provider enum the feed has delivered + its mapping —
    the Settings screen renders one row per observed value so the
    user maps each enum to one of THEIR categories once (issue #453)."""
    mapping = {(m.provider, m.provider_category): m.category_id
               for m in session.scalars(
                   select(ProviderCategoryMap).where(
                       ProviderCategoryMap.household_id == household.id))}
    rows = session.execute(
        select(Transaction.source, Transaction.provider_category,
               func.count(Transaction.id))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household.id,
               Transaction.provider_category.isnot(None))
        .group_by(Transaction.source, Transaction.provider_category)
        .order_by(Transaction.provider_category)).all()
    return [schemas.ObservedProviderCategoryOut(
        provider=str(source.value), provider_category=str(cat),
        transaction_count=count,
        mapped_category_id=mapping.get((str(source.value), str(cat))))
        for source, cat, count in rows]


@router.put("/provider-categories",
            response_model=list[schemas.ProviderCategoryMapOut])
def replace_provider_categories(
    body: list[schemas.ProviderCategoryMapIn], household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> list[schemas.ProviderCategoryMapOut]:
    """Replace the household's provider-category map (issue #453), then
    re-apply it so uncategorised transactions pick up the new mapping
    immediately. Rows already categorised keep their value — the map
    only ever fills ``category_id IS NULL``."""
    seen: set[tuple[str, str]] = set()
    for entry in body:
        key = (entry.provider, entry.provider_category)
        if key in seen:
            raise HTTPException(422, f"duplicate mapping {key}")
        seen.add(key)
        _category(session, household.id, entry.category_id)
    session.execute(
        delete(ProviderCategoryMap).where(
            ProviderCategoryMap.household_id == household.id))
    rows = [ProviderCategoryMap(
        household_id=household.id, provider=e.provider,
        provider_category=e.provider_category, category_id=e.category_id)
        for e in body]
    session.add_all(rows)
    session.flush()
    applied = categorise.apply_provider_map(session, household.id)
    emit(session, household.id, actor, "update",
         "provider_category_map", 0,
         {"entries": len(rows), "applied": applied})
    return [schemas.ProviderCategoryMapOut.model_validate(r)
            for r in rows]


@router.post("/auto-refund", response_model=schemas.PairRefundOut)
def auto_refund(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    since: Annotated[date | None, Query()] = None,
) -> schemas.PairRefundOut:
    """Link positive txns to earlier same-payee debits as refunds."""
    n = categorise.auto_link_refunds(session, household.id, since)
    emit(session, household.id, actor, "auto_refund",
         "transaction", 0, {"linked": n})
    return schemas.PairRefundOut(linked=n)
