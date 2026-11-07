"""Subscription register (#107): CRUD + renewals + cancellation +
price history."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import detection, schedule
from app.domain import subscriptions as subs
from app.persistence.models import (
    DetectionCandidate,
    PriceChangeEvent,
    Subscription,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _get(
    session: Session, household_id: int, sid: int
) -> Subscription:
    s = session.get(Subscription, sid)
    if s is None or s.household_id != household_id:
        raise HTTPException(404, f"subscription {sid} not found")
    return s


def _out(s: Subscription) -> schemas.SubscriptionOut:
    out = schemas.SubscriptionOut.model_validate(s)
    out.overdue = subs.renewal_overdue(s, date.today())
    return out


@router.get("", response_model=list[schemas.SubscriptionOut])
def list_subscriptions(
    household: HouseholdDep, session: SessionDep,
    include_cancelled: Annotated[bool, Query()] = True,
) -> list[schemas.SubscriptionOut]:
    rows = (subs.all_subscriptions(session, household.id)
            if include_cancelled
            else subs.active_subscriptions(session, household.id))
    return [_out(s) for s in rows]


@router.post("", response_model=schemas.SubscriptionOut,
             status_code=201)
def create_subscription(
    body: schemas.SubscriptionIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.SubscriptionOut:
    s = subs.create(
        session, household.id, payee=body.payee,
        frequency=body.frequency,
        expected_pence=body.expected.pence,
        next_renewal_date=body.next_renewal_date,
        category_id=body.category_id, account_id=body.account_id,
        tolerance_pct=body.tolerance_pct, notes=body.notes)
    schedule.rematerialize_subscription(session, s)
    emit(session, household.id, actor, "create", "subscription", s.id,
         {"payee": s.payee, "frequency": s.frequency.value})
    return _out(s)


def _get_candidate(
    session: Session, household_id: int, cid: int
) -> DetectionCandidate:
    c = session.get(DetectionCandidate, cid)
    if c is None or c.household_id != household_id:
        raise HTTPException(404, f"candidate {cid} not found")
    return c


@router.post(
    "/detect",
    response_model=list[schemas.DetectionCandidateOut])
def detect_candidates(
    body: schemas.DetectionScanIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> list[schemas.DetectionCandidateOut]:
    found = detection.scan(
        session, household.id,
        min_occurrences=body.min_occurrences)
    if found:
        emit(session, household.id, actor, "detect",
             "detection_candidate", 0,
             {"touched": len(found)})
    return [
        schemas.DetectionCandidateOut.model_validate(c)
        for c in found]


@router.get(
    "/candidates",
    response_model=list[schemas.DetectionCandidateOut])
def list_candidates(
    household: HouseholdDep, session: SessionDep,
    include_dismissed: Annotated[bool, Query()] = False,
) -> list[schemas.DetectionCandidateOut]:
    rows = detection.candidates(
        session, household.id, include_dismissed=include_dismissed)
    return [schemas.DetectionCandidateOut.model_validate(c)
            for c in rows]


@router.get(
    "/price-changes",
    response_model=list[schemas.PriceChangeEventOut])
def price_change_feed(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.PriceChangeEventOut]:
    """Household-level alert feed — every detected price move."""
    rows = session.execute(
        select(PriceChangeEvent)
        .where(PriceChangeEvent.household_id == household.id)
        .order_by(PriceChangeEvent.detected_date.desc(),
                  PriceChangeEvent.id)
    ).scalars().all()
    return [schemas.PriceChangeEventOut.model_validate(e)
            for e in rows]


@router.post(
    "/candidates/{cid}/confirm",
    response_model=schemas.SubscriptionOut, status_code=201)
def confirm_candidate(
    cid: int, body: schemas.CandidateConfirmIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SubscriptionOut:
    c = _get_candidate(session, household.id, cid)
    s = detection.confirm(
        session, c, category_id=body.category_id,
        account_id=body.account_id,
        tolerance_pct=body.tolerance_pct)
    schedule.rematerialize_subscription(session, s)
    emit(session, household.id, actor, "confirm",
         "detection_candidate", c.id,
         {"subscription_id": s.id})
    return _out(s)


@router.post(
    "/candidates/{cid}/dismiss",
    response_model=schemas.DetectionCandidateOut)
def dismiss_candidate(
    cid: int, body: schemas.CandidateDismissIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.DetectionCandidateOut:
    c = _get_candidate(session, household.id, cid)
    detection.dismiss(c, body.reason)
    session.flush()
    emit(session, household.id, actor, "dismiss",
         "detection_candidate", c.id, {"reason": body.reason})
    return schemas.DetectionCandidateOut.model_validate(c)


@router.get("/{sid}", response_model=schemas.SubscriptionOut)
def get_subscription(
    sid: int, household: HouseholdDep, session: SessionDep,
) -> schemas.SubscriptionOut:
    return _out(_get(session, household.id, sid))


@router.patch("/{sid}", response_model=schemas.SubscriptionOut)
def patch_subscription(
    sid: int, body: schemas.SubscriptionPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SubscriptionOut:
    s = _get(session, household.id, sid)
    changed = body.model_dump(exclude_unset=True)
    if "expected" in changed:
        changed["expected_pence"] = changed.pop("expected").pence
    if "payee" in changed:
        changed["payee_normalised"] = subs.normalise_payee(
            changed["payee"])
    if "next_renewal_date" in changed:
        changed["anchor_day"] = changed["next_renewal_date"].day
    for k, v in changed.items():
        setattr(s, k, v)
    session.flush()
    schedule.rematerialize_subscription(session, s)
    emit(session, household.id, actor, "update", "subscription", s.id,
         changed)
    return _out(s)


@router.post("/{sid}/cancel", response_model=schemas.SubscriptionOut)
def cancel_subscription(
    sid: int, body: schemas.SubscriptionCancelIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SubscriptionOut:
    s = _get(session, household.id, sid)
    subs.cancel(s, body.date)
    schedule.rematerialize_subscription(session, s)
    emit(session, household.id, actor, "cancel", "subscription", s.id,
         {"date": body.date.isoformat()})
    return _out(s)


@router.get("/{sid}/prices",
            response_model=list[schemas.SubscriptionPriceOut])
def price_history(
    sid: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.SubscriptionPriceOut]:
    s = _get(session, household.id, sid)
    return [schemas.SubscriptionPriceOut.model_validate(p)
            for p in s.prices]


@router.get("/{sid}/price-changes",
            response_model=list[schemas.PriceChangeEventOut])
def price_changes(
    sid: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.PriceChangeEventOut]:
    s = _get(session, household.id, sid)
    return [schemas.PriceChangeEventOut.model_validate(e)
            for e in s.price_changes]


@router.post("/{sid}/payments",
             response_model=schemas.SubscriptionPriceOut,
             status_code=201)
def record_payment(
    sid: int, body: schemas.SubscriptionPaymentIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SubscriptionPriceOut:
    s = _get(session, household.id, sid)
    p = subs.record_price(
        session, s, body.date, body.amount.pence,
        transaction_id=body.transaction_id)
    schedule.rematerialize_subscription(session, s)
    emit(session, household.id, actor, "record_payment",
         "subscription", s.id, {"date": body.date.isoformat()})
    return schemas.SubscriptionPriceOut.model_validate(p)
