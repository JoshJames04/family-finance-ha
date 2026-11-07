"""Issue #103 — golden estimation: exact expected pence for synthetic
consumption × tariff, incl. standing charge + a mid-month rate change,
millipence rate precision, and missing-day handling."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.domain import energy
from app.domain.energy import EnergyError
from app.persistence.models import (
    EnergyReading,
    EnergyTariff,
    Household,
)


def _tariff(session: Session, hid: int, effective: date,
            mp_per_kwh: int, standing_pence: int) -> None:
    session.add(EnergyTariff(
        household_id=hid, effective_from=effective,
        unit_rate_millipence=mp_per_kwh,
        standing_charge_pence=standing_pence))


def _readings(session: Session, hid: int, start: date, days: int,
              wh: int = 10_000) -> None:
    for i in range(days):
        session.add(EnergyReading(
            household_id=hid, date=start + timedelta(days=i), wh=wh,
            source="ha_statistics"))
    session.flush()


class TestGoldenEstimate:
    """25p/kWh + 50p/day until Jan 14, then 30p/kWh + 55p/day; 10 kWh
    every day of January."""

    @pytest.fixture(autouse=True)
    def _setup(self, session: Session, household: Household) -> None:
        hid = household.id
        _tariff(session, hid, date(2026, 1, 1), 25_000, 50)
        _tariff(session, hid, date(2026, 1, 15), 30_000, 55)
        _readings(session, hid, date(2026, 1, 1), 31)
        session.flush()
        self.hid = hid

    def test_total_is_exact(self, session: Session) -> None:
        est = energy.estimate(session, self.hid,
                              date(2026, 1, 1), date(2026, 1, 31))
        # 14×(10kWh×25p + 50p) + 17×(10×30p + 55p)
        assert est.unit_pence == 14 * 250 + 17 * 300
        assert est.standing_pence == 14 * 50 + 17 * 55
        assert est.total_pence == 4_200 + 6_035
        assert est.missing_days == 0
        assert len(est.days) == 31

    def test_rate_change_boundary(self, session: Session) -> None:
        est = energy.estimate(session, self.hid,
                              date(2026, 1, 14), date(2026, 1, 15))
        d14, d15 = est.days
        assert d14.unit_pence == 250 and d14.standing_pence == 50
        assert d15.unit_pence == 300 and d15.standing_pence == 55
        assert d14.tariff_id != d15.tariff_id


class TestMillipencePrecision:
    def test_fractional_rate(self, session: Session,
                             household: Household) -> None:
        hid = household.id
        _tariff(session, hid, date(2026, 3, 1), 24_501, 60)
        _readings(session, hid, date(2026, 3, 1), 1)
        session.flush()
        est = energy.estimate(session, hid,
                              date(2026, 3, 1), date(2026, 3, 1))
        # 10 kWh × 24.501p = 245.01p → rounds half-up to 245p
        assert est.unit_pence == 245
        assert est.total_pence == 245 + 60


class TestEdgeCases:
    def test_missing_day_counts_standing_only(
            self, session: Session, household: Household) -> None:
        hid = household.id
        _tariff(session, hid, date(2026, 4, 1), 20_000, 40)
        session.add(EnergyReading(household_id=hid,
                                  date=date(2026, 4, 2), wh=5_000,
                                  source="manual"))
        session.flush()
        est = energy.estimate(session, hid,
                              date(2026, 4, 1), date(2026, 4, 3))
        assert est.missing_days == 2
        assert est.unit_pence == 100  # 5 kWh × 20p on Apr 2 only
        assert est.standing_pence == 3 * 40

    def test_no_tariff_raises(self, session: Session,
                              household: Household) -> None:
        with pytest.raises(EnergyError):
            energy.estimate(session, household.id,
                            date(2026, 1, 1), date(2026, 1, 2))

    def test_reversed_window_rejected(self, session: Session,
                                      household: Household) -> None:
        with pytest.raises(EnergyError):
            energy.estimate(session, household.id,
                            date(2026, 1, 2), date(2026, 1, 1))


class TestMonthReport:
    """Issue #104 — deterministic MTD-vs-budget + month-end projection
    on a 30-day fixture month viewed on day 10."""

    @pytest.fixture(autouse=True)
    def _setup(self, session: Session, household: Household) -> None:
        from app.persistence.models import (
            Budget,
            BudgetLine,
            BudgetLineSide,
            BudgetPeriodType,
            BudgetStatus,
            Category,
            EnergyConfig,
        )
        hid = household.id
        _tariff(session, hid, date(2026, 5, 1), 20_000, 40)
        _readings(session, hid, date(2026, 5, 1), 10)
        cat = Category(household_id=hid, name="Energy")
        session.add(cat)
        session.flush()
        b = Budget(household_id=hid, name="May",
                   period_type=BudgetPeriodType.monthly,
                   start_date=date(2026, 5, 1),
                   end_date=date(2026, 5, 31),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        session.add(BudgetLine(
            budget_id=b.id, category_id=cat.id,
            side=BudgetLineSide.expense, original_pence=10_000))
        session.add(EnergyConfig(household_id=hid,
                                 budget_category_id=cat.id))
        session.flush()
        self.hid = hid

    def test_golden_report(self, session: Session) -> None:
        r = energy.month_report(session, self.hid, date(2026, 5, 10))
        # MTD: 10 days × (10kWh×20p + 40p) = 2400p
        assert r.estimate.total_pence == 2_400
        assert r.budget_pence == 10_000
        assert r.variance_pence == 2_400 - 10_000
        # Projection: avg 10 kWh/day × 21 remaining days (11th–31st) at
        # current tariff + standing → 2400 + 21×240 = 7440
        assert r.avg_daily_wh == 10_000
        assert r.projected_total_pence == 7_440
        assert r.projected_variance_pence == 7_440 - 10_000
        assert r.through == date(2026, 5, 10)
        assert r.month_end == date(2026, 5, 31)

    def test_past_month_no_projection(self, session: Session) -> None:
        r = energy.month_report(session, self.hid, date(2026, 5, 31))
        assert r.projected_total_pence == r.estimate.total_pence

    def test_no_budget_link_gives_nulls(self, session: Session,
                                        household: Household) -> None:
        from app.persistence.models import EnergyConfig
        cfg = session.get(EnergyConfig, 1)
        if cfg is not None:
            cfg.budget_category_id = None
            session.flush()
        r = energy.month_report(session, self.hid, date(2026, 5, 10))
        assert r.budget_pence is None
        assert r.variance_pence is None
        assert r.projected_variance_pence is None
