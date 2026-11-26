"""Seasonal and payroll branches must upgrade together without rewriting history."""

from argparse import Namespace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.persistence.migrate import (
    alembic_config,
    current_revisions,
    downgrade_one,
    migrations_current,
)

MERGED_HEAD = "0075_merge_seasonal_payroll"
STATEMENT_IDENTITY = "0076_statement_bank_identity"
HEAD = "0077_bill_occurrence_origin"
SEASONAL = "0071_seasonal_event_date"
BONUSES = "0074_employer_bonuses"


def test_migration_history_has_one_head_and_keeps_existing_parents(tmp_path):
    script = ScriptDirectory.from_config(alembic_config(f"sqlite:///{tmp_path / 'graph.db'}"))
    assert script.get_heads() == [HEAD]
    assert script.get_revision(HEAD).down_revision == STATEMENT_IDENTITY
    assert script.get_revision(STATEMENT_IDENTITY).down_revision == MERGED_HEAD
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
        assert migrations_current(engine, url)
        with engine.connect() as conn:
            versions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            assert versions == [HEAD]
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
            assert not migrations_current(engine, url)
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
        assert versions == [HEAD]
    assert "event_date" in {c["name"] for c in inspect(engine).get_columns("seasonal_event")}
    assert "employment_bonus" in inspect(engine).get_table_names()
    engine.dispose()


def test_single_node_steps_keep_the_other_branch_until_the_shared_parent(tmp_path):
    url = f"sqlite:///{tmp_path / 'steps.db'}"
    config = alembic_config(url)
    command.upgrade(config, MERGED_HEAD)
    engine = create_engine(url)
    for expected in [
        {SEASONAL, BONUSES},
        {SEASONAL, "0073_payroll_verified_inputs"},
        {SEASONAL},
        {"0070_transfer_confirmation_history"},
    ]:
        downgrade_one(config)
        assert set(current_revisions(engine)) == expected
    engine.dispose()


def test_rollback_honours_x_database_override_before_environment_and_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.db"
    env_path = tmp_path / "env.db"
    actual_path = tmp_path / "override.db"
    config = alembic_config(f"sqlite:///{config_path}")
    monkeypatch.setenv("FF_DATABASE_URL", f"sqlite:///{env_path}")
    config.cmd_opts = Namespace(x=[f"db_url=sqlite:///{actual_path}"])
    command.upgrade(config, MERGED_HEAD)
    downgrade_one(config)
    engine = create_engine(f"sqlite:///{actual_path}")
    assert set(current_revisions(engine)) == {SEASONAL, BONUSES}
    assert not config_path.exists()
    assert not env_path.exists()
    engine.dispose()


def test_legacy_bill_origin_backfill_rejects_reused_ids_and_preserves_live_history(tmp_path):
    from json import dumps

    url = f"sqlite:///{tmp_path / 'origin.db'}"
    config = alembic_config(url)
    command.upgrade(config, STATEMENT_IDENTITY)
    engine = create_engine(url)
    birth = "2026-01-01 12:00:00"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO household (id,name) VALUES (1,'Synthetic')"))
        for mid in (5, 6, 7):
            conn.execute(text("""
                INSERT INTO expected_movement
                (id,household_id,expected_date,amount_pence,label,confidence,source_type,
                 source_id,created_at)
                VALUES (:id,1,'2026-02-05',-100,'Synthetic','scheduled','commitment',:id,:birth)
            """), {"id": mid, "birth": birth})
        def event(mid, action, kind, at, payload):
            conn.execute(text("""
                INSERT INTO audit_event (household_id,actor,action,entity_type,entity_id,
                                         payload,created_at)
                VALUES (1,'Synthetic',:action,:kind,:mid,:payload,:at)
            """), {"mid": mid, "action": action, "kind": kind, "at": at, "payload": dumps(payload)})
        for mid in (5, 6, 7):
            at = "2026-01-01 12:00:01" if mid == 5 else birth
            event(mid, "reschedule", "expected_movement", at,
                  {"from": "2026-02-01", "to": "2026-02-03"})
            event(mid, "reschedule", "expected_movement", at,
                  {"from": "2026-02-03", "to": "2026-02-05"})
        # The replacement has the same numeric IDs and due date as the deleted bill.
        event(6, "delete", "recurring_commitment", birth, {})
        event(6, "create", "recurring_commitment", birth, {})
    command.upgrade(config, HEAD)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, original_expected_date FROM expected_movement "
                                 "ORDER BY id")).all() == [(5, "2026-02-01"), (6, None), (7, None)]
    command.downgrade(config, STATEMENT_IDENTITY)
    assert "original_expected_date" not in {c["name"] for c in
                                            inspect(engine).get_columns("expected_movement")}
    command.upgrade(config, HEAD)
    engine.dispose()
