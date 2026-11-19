"""Seasonal and payroll branches must upgrade together without rewriting history."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.persistence.migrate import alembic_config

MERGED_HEAD = "0075_merge_seasonal_payroll"
SEASONAL = "0071_seasonal_event_date"
BONUSES = "0074_employer_bonuses"


def test_migration_history_has_one_head_and_keeps_existing_parents(tmp_path):
    script = ScriptDirectory.from_config(alembic_config(f"sqlite:///{tmp_path / 'graph.db'}"))
    assert script.get_heads() == [MERGED_HEAD]
    assert script.get_revision(SEASONAL).down_revision == "0070_transfer_confirmation_history"
    assert script.get_revision(BONUSES).down_revision == "0073_payroll_verified_inputs"
    assert set(script.get_revision(MERGED_HEAD).down_revision) == {SEASONAL, BONUSES}


@pytest.mark.parametrize("starting_revisions", [
    (), ("0070_transfer_confirmation_history",), (SEASONAL,),
    ("0073_payroll_verified_inputs",), (BONUSES,), (SEASONAL, BONUSES),
])
def test_prior_branches_roundtrip_without_losing_rows(tmp_path, starting_revisions):
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    config = alembic_config(url)
    for revision in starting_revisions:
        command.upgrade(config, revision)
    if not starting_revisions:
        command.upgrade(config, "0070_transfer_confirmation_history")
    engine = create_engine(url)
    original_date = "2026-12-25" if "event_date" in {
        c["name"] for c in inspect(engine).get_columns("seasonal_event")
    } else None
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO household (id,name) VALUES (1,'Synthetic migration')"))
        conn.execute(text("INSERT INTO seasonal_event "
                          "(id,household_id,year,type,name,budget_pence) "
                          "VALUES (1,1,2026,'other','Preserved synthetic plan',12345)"))
        if original_date:
            conn.execute(text("UPDATE seasonal_event SET event_date=:date"),
                         {"date": original_date})
    for roundtrip in range(2):
        command.upgrade(config, "head")
        with engine.connect() as conn:
            versions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            assert versions == [MERGED_HEAD]
            row = conn.execute(text(
                "SELECT name,budget_pence,event_date FROM seasonal_event")).one()
            assert tuple(row) == ("Preserved synthetic plan", 12345, original_date)
        assert "employment_bonus" in inspect(engine).get_table_names()
        assert "verified_ytd_tax_pence" in {
            c["name"] for c in inspect(engine).get_columns("payroll_run")}
        assert "share_payment_method" in {
            c["name"] for c in inspect(engine).get_columns("employment")}
        if roundtrip == 0:
            # A mergepoint has two parents: bare -1 is ambiguous. Targeting
            # one immediate parent removes only the merge node, retaining both
            # schema branches and their rows for the subsequent upgrade.
            command.downgrade(config, BONUSES)
            with engine.connect() as conn:
                assert set(conn.execute(text("SELECT version_num FROM alembic_version"))
                           .scalars().all()) == {SEASONAL, BONUSES}
    engine.dispose()


def test_fresh_database_upgrades_directly_to_combined_head(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        assert versions == [MERGED_HEAD]
    assert "event_date" in {c["name"] for c in inspect(engine).get_columns("seasonal_event")}
    assert "employment_bonus" in inspect(engine).get_table_names()
    engine.dispose()
