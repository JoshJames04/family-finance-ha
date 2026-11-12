"""Energy tariffs, consumption readings, cost estimation (#103)."""
from __future__ import annotations

from datetime import date as date_t

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import energy, outbox, rbac
from app.persistence.models import (
    EnergyConfig,
    EnergyReading,
    EnergyTariff,
)

router = APIRouter(prefix="/energy", tags=["energy"])


@router.get("/tariffs", response_model=list[schemas.EnergyTariffOut])
def list_tariffs(household: HouseholdDep,
                 session: SessionDep) -> list[EnergyTariff]:
    return list(session.execute(
        select(EnergyTariff)
        .where(EnergyTariff.household_id == household.id)
        .order_by(EnergyTariff.effective_from)).scalars())


@router.post("/tariffs", response_model=schemas.EnergyTariffOut,
             status_code=201)
def upsert_tariff(body: schemas.EnergyTariffIn,
                  household: HouseholdDep, session: SessionDep,
                  actor: ActorDep) -> EnergyTariff:
    """Effective-dated upsert — posting the same date replaces the rate
    (corrections), a later date supersedes (a real price change)."""
    t = session.execute(
        select(EnergyTariff).where(
            EnergyTariff.household_id == household.id,
            EnergyTariff.effective_from == body.effective_from)
    ).scalar_one_or_none()
    if t is None:
        t = EnergyTariff(household_id=household.id,
                         effective_from=body.effective_from,
                         unit_rate_millipence=body.unit_rate_millipence,
                         standing_charge_pence=body.standing_charge.pence)
        session.add(t)
    else:
        t.unit_rate_millipence = body.unit_rate_millipence
        t.standing_charge_pence = body.standing_charge.pence
    session.flush()
    emit(session, household.id, actor, "upsert", "energy_tariff", t.id,
         {"effective_from": str(t.effective_from)})
    return t


@router.get("/readings", response_model=list[schemas.EnergyReadingOut])
def list_readings(
        household: HouseholdDep, session: SessionDep,
        start: date_t | None = None,
        end: date_t | None = None) -> list[EnergyReading]:
    q = select(EnergyReading).where(
        EnergyReading.household_id == household.id)
    if start:
        q = q.where(EnergyReading.date >= start)
    if end:
        q = q.where(EnergyReading.date <= end)
    return list(session.execute(
        q.order_by(EnergyReading.date)).scalars())


@router.post("/readings", response_model=schemas.EnergyReadingOut,
             status_code=201)
def upsert_reading(body: schemas.EnergyReadingIn,
                   household: HouseholdDep, session: SessionDep,
                   actor: ActorDep) -> EnergyReading:
    """Idempotent per-day ingest — the HA statistics feed or a manual
    entry can re-post the same day safely; last write wins."""
    r = session.execute(
        select(EnergyReading).where(
            EnergyReading.household_id == household.id,
            EnergyReading.date == body.date)).scalar_one_or_none()
    changed = r is None or r.wh != body.wh or r.source != body.source
    if r is None:
        r = EnergyReading(household_id=household.id, date=body.date,
                          wh=body.wh, source=body.source)
        session.add(r)
    else:
        r.wh = body.wh
        r.source = body.source
    session.flush()
    emit(session, household.id, actor, "upsert", "energy_reading", r.id,
         {"date": str(r.date), "source": r.source})
    if changed:
        outbox.emit(session, household.id, "energy.reading_updated", entity_kind="energy_reading",
                    entity_id=r.id, payload={"date": str(r.date), "source": r.source})
    return r


@router.get("/estimate", response_model=schemas.EnergyEstimateOut)
def get_estimate(
        household: HouseholdDep, session: SessionDep,
        start: date_t | None = None,
        end: date_t | None = None) -> schemas.EnergyEstimateOut:
    """Estimated cost over an inclusive window (default: current
    month-to-date). Priced per-day under the effective tariff so a
    mid-window rate change splits naturally."""
    if start is None or end is None:
        today = date_t.today()
        start = start or today.replace(day=1)
        end = end or today
    est = energy.estimate(session, household.id, start, end)
    return schemas.EnergyEstimateOut(
        start=est.start, end=est.end,
        days=[schemas.EnergyDayOut(**vars(d)) for d in est.days],
        unit_pence=est.unit_pence, standing_pence=est.standing_pence,
        total_pence=est.total_pence, missing_days=est.missing_days)


@router.get("/config", response_model=schemas.EnergyConfigOut)
def get_config(household: HouseholdDep,
               session: SessionDep) -> schemas.EnergyConfigOut:
    cfg = session.execute(
        select(EnergyConfig).where(
            EnergyConfig.household_id == household.id)
    ).scalar_one_or_none()
    return schemas.EnergyConfigOut(
        meter_entity=cfg.meter_entity if cfg else None,
        budget_category_id=cfg.budget_category_id if cfg else None)


@router.put("/config", response_model=schemas.EnergyConfigOut)
def put_config(body: schemas.EnergyConfigIn,
               household: HouseholdDep, session: SessionDep,
               actor: ActorDep) -> EnergyConfig:
    cfg = session.execute(
        select(EnergyConfig).where(
            EnergyConfig.household_id == household.id)
    ).scalar_one_or_none()
    if cfg is None:
        cfg = EnergyConfig(household_id=household.id,
                           meter_entity=body.meter_entity,
                           budget_category_id=body.budget_category_id)
        session.add(cfg)
    else:
        cfg.meter_entity = body.meter_entity
        cfg.budget_category_id = body.budget_category_id
    session.flush()
    emit(session, household.id, actor, "upsert", "energy_config", cfg.id,
         {"meter_entity": cfg.meter_entity})
    return cfg


@router.get("/report", response_model=schemas.EnergyMonthReportOut)
def get_report(
        household: HouseholdDep, session: SessionDep,
        as_of: date_t | None = None,
        ) -> schemas.EnergyMonthReportOut:
    """Month-to-date estimate vs the configured energy budget with a
    month-end projection (issue #104). The projection extrapolates the
    average metered daily consumption at the current tariff."""
    r = energy.month_report(session, household.id, as_of)
    est = r.estimate
    return schemas.EnergyMonthReportOut(
        month_start=r.month_start, month_end=r.month_end,
        through=r.through, unit_pence=est.unit_pence,
        standing_pence=est.standing_pence, total_pence=est.total_pence,
        missing_days=est.missing_days, budget_pence=r.budget_pence,
        variance_pence=r.variance_pence,
        projected_total_pence=r.projected_total_pence,
        projected_variance_pence=r.projected_variance_pence,
        avg_daily_wh=r.avg_daily_wh)


@router.delete("/tariffs/{tariff_id}", status_code=204)
def delete_tariff(
    tariff_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> Response:
    tariff = session.scalar(select(EnergyTariff).where(
        EnergyTariff.id == tariff_id, EnergyTariff.household_id == household.id))
    if tariff is None:
        raise HTTPException(404, "tariff not found")
    if not rbac.write_allowed(None, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    emit(session, household.id, actor, "delete", "energy_tariff", tariff.id,
         {"effective_from": str(tariff.effective_from)})
    session.delete(tariff)
    session.flush()
    return Response(status_code=204)
