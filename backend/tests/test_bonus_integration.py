"""Cross-month and migration safeguards for employer bonus planning."""

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.domain import bonuses, payrun, schedule
from app.persistence.models import Account, ExpectedMovement, ExpectedSource, PayrollRun
from tests import test_employer_bonuses as bonus_fixtures

employer = bonus_fixtures.employer


def migration_config(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    url = f"sqlite:///{tmp_path / 'bonus-migrations.db'}"
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_bonus_migration_empty_roundtrip_has_one_head(tmp_path, monkeypatch):
    monkeypatch.delenv("FF_DATABASE_URL", raising=False)
    config, url = migration_config(tmp_path)
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    assert "employment_bonus" in sa.inspect(engine).get_table_names()
    command.downgrade(config, "-1")
    assert "employment_bonus" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).all() == [
            ("0072_employer_bonuses",)
        ]
    assert "shares_owned" in {c["name"] for c in sa.inspect(engine).get_columns("salary_revision")}
    engine.dispose()


def test_bonus_migration_refuses_to_drop_bonus_history(tmp_path, monkeypatch):
    monkeypatch.delenv("FF_DATABASE_URL", raising=False)
    config, url = migration_config(tmp_path)
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    table = sa.Table("employment_bonus", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(
            employment_id=1, pay_month=date(2025, 4, 1), pay_date=date(2025, 4, 28),
            label="Migration guard", salary_percentage=None, share_value_pence=None,
            manual_pence=100, salary_basis_pence=0, shares_owned=0,
            salary_bonus_pence=0, share_bonus_pence=0, gross_pence=100,
            noncash_pence=0, share_payment_method="payroll_cash",
        ))
    with pytest.raises(RuntimeError, match="history|backup"):
        command.downgrade(config, "-1")
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT COUNT(*) FROM employment_bonus")) == 1
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "0072_employer_bonuses"
        )
    engine.dispose()


def test_earlier_bonus_recalculates_later_cumulative_tax_in_paydate_order(session, employer):
    june = payrun.generate(
        session, employer, start=date(2025, 6, 1), end=date(2025, 6, 30),
        pay_date=date(2025, 6, 27),
    )
    may = payrun.generate(
        session, employer, start=date(2025, 5, 1), end=date(2025, 5, 31),
        pay_date=date(2025, 5, 28),
    )
    old_may_net = may.net_pence
    bonuses.save(session, employer, pay_month=date(2025, 4, 1), manual_pence=3_000_000)
    assert may.net_pence != old_may_net
    for run in (may, june):
        expected, _ = payrun.deductions_for_pay(
            session, employer, run.gross_pence, run.period_end, run.pay_date,
        )
        assert run.net_pence == expected.net_pence
    before = (may.net_pence, june.net_pence)
    bonuses.refresh(session, employer, date(2025, 4, 1))
    assert (may.net_pence, june.net_pence) == before


def test_bonus_replaces_projected_salary_with_one_updated_net_movement(
    session, household, employer, monkeypatch,
):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 4, 1)

    monkeypatch.setattr(schedule, "date", FrozenDate)
    account = Account(
        household_id=household.id, name="Salary account", type="joint",
        opening_date=date(2025, 1, 1),
    )
    session.add(account)
    session.flush()
    employer.paid_into_account_id = account.id
    schedule.rematerialize_employment(session, employer, horizon_days=35)
    projected = session.scalars(sa.select(ExpectedMovement).where(
        ExpectedMovement.expected_date == date(2025, 4, 28),
    )).one()
    assert projected.source_type == ExpectedSource.employment
    original_net = projected.amount_pence
    entry = bonuses.save(
        session, employer, pay_month=date(2025, 4, 1), manual_pence=100_000,
    )
    run = session.scalar(sa.select(PayrollRun))
    movements = session.scalars(sa.select(ExpectedMovement).where(
        ExpectedMovement.expected_date == date(2025, 4, 28),
    )).all()
    assert len(movements) == 1
    assert movements[0].source_type == ExpectedSource.payroll_run
    assert movements[0].source_id == run.id
    assert movements[0].account_id == account.id
    assert movements[0].amount_pence == run.net_pence > original_net
    bonuses.remove(session, employer, entry)
    updated = session.scalars(sa.select(ExpectedMovement).where(
        ExpectedMovement.expected_date == date(2025, 4, 28),
    )).all()
    assert len(updated) == 1
    expected, _ = payrun.deductions_for_pay(
        session, employer, run.gross_pence, run.period_end, run.pay_date,
    )
    assert updated[0].amount_pence == run.net_pence == expected.net_pence
    assert run.planned_bonus_pence == 0


@pytest.mark.parametrize("saved_data", ["holding", "payment_method", "planned_run", "noncash_run"])
def test_bonus_migration_refuses_to_drop_other_new_data(tmp_path, monkeypatch, saved_data):
    from sqlalchemy.orm import Session

    from app.persistence.models import Employment, Household, SalaryRevision, UserProfile

    monkeypatch.delenv("FF_DATABASE_URL", raising=False)
    config, url = migration_config(tmp_path)
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    with Session(engine) as db:
        household = Household(name="Migration guard")
        db.add(household)
        db.flush()
        owner = UserProfile(household_id=household.id, display_name="Owner")
        db.add(owner)
        db.flush()
        employment = Employment(
            household_id=household.id, profile_id=owner.id, employer="Guard",
            jurisdiction="england_wales", contracted_hours=37.5,
        )
        db.add(employment)
        db.flush()
        if saved_data == "holding":
            db.add(SalaryRevision(
                employment_id=employment.id, effective_date=date(2025, 4, 1),
                annual_salary_pence=0, shares_owned=1,
            ))
        elif saved_data == "payment_method":
            employment.share_payment_method = "payroll_noncash"
        else:
            db.add(PayrollRun(
                employment_id=employment.id, period_start=date(2025, 4, 1),
                period_end=date(2025, 4, 30), pay_date=date(2025, 4, 28),
                tax_year="2025-26", rule_set_version=0, gross_pence=100, net_pence=100,
                planned_bonus_pence=100 if saved_data == "planned_run" else 0,
                noncash_bonus_pence=100 if saved_data == "noncash_run" else 0,
            ))
        db.commit()
    with pytest.raises(RuntimeError, match="history|backup|data"):
        command.downgrade(config, "-1")
    assert "employment_bonus" in sa.inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "0072_employer_bonuses"
        )
    engine.dispose()
