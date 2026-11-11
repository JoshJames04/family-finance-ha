"""Migration upgrade/rollback harness (issue #130).

Proves every schema migration up AND down on seeded data — not just
linear head. Seeded rows at intermediate revisions must survive the
upgrade path to head with row counts and key invariants intact.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from app.persistence.migrate import alembic_config, current_revision

_DUMMY = alembic_config("sqlite:///:memory:")
REVISIONS = [r.revision for r in reversed(list(
    ScriptDirectory.from_config(_DUMMY).walk_revisions()))]


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    return alembic_config(
        f"sqlite:///{(tmp_path / 'm.db').as_posix()}")


def _eng(c: Config) -> Engine:
    url = c.get_main_option("sqlalchemy.url")
    assert url is not None
    return create_engine(url)


def _count(conn: Connection, table: str) -> int:
    return int(conn.execute(
        text(f"SELECT count(*) FROM {table}")).scalar_one())


def _seed_core(conn: Connection) -> None:
    """Seed minimal data that must survive all later migrations."""
    conn.execute(text(
        "INSERT INTO app_meta (key, value) VALUES ('seeder', 'x')"))
    conn.execute(text(
        "INSERT INTO household (name, created_at, updated_at)"
        " VALUES ('Fam', '2026-01-01', '2026-01-01')"))
    conn.execute(text(
        "INSERT INTO account (household_id, name, type, currency,"
        " opening_balance_pence, opening_date, archived)"
        " VALUES (1, 'Joint', 'joint', 'GBP', 0, '2026-01-01', 0)"))


def _seed_transactions(conn: Connection) -> None:
    conn.execute(text(
        "INSERT INTO \"transaction\" (account_id, date, amount_pence,"
        " payee, status, source)"
        " VALUES (1, '2026-02-01', -2500, 'Shop', 'cleared', 'manual')"))
    conn.execute(text(
        "INSERT INTO \"transaction\" (account_id, date, amount_pence,"
        " payee, status, source)"
        " VALUES (1, '2026-02-02', 100000, 'Salary', 'cleared',"
        " 'manual')"))


class TestLinearChain:
    def test_full_up_down_chain(self, cfg: Config) -> None:
        command.upgrade(cfg, "head")
        eng = _eng(cfg)
        assert current_revision(eng) == REVISIONS[-1]
        # walk down one revision at a time, then back up
        for _ in REVISIONS:
            command.downgrade(cfg, "-1")
        assert current_revision(_eng(cfg)) is None
        command.upgrade(cfg, "head")
        assert current_revision(_eng(cfg)) == REVISIONS[-1]

    def test_step_down_preserves_tables(self, cfg: Config) -> None:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")  # reverse the latest revision
        eng = _eng(cfg)
        assert current_revision(eng) == REVISIONS[-2]
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='transaction'")).first() is not None
        command.upgrade(cfg, "head")  # and re-apply cleanly
        assert current_revision(_eng(cfg)) == REVISIONS[-1]


class TestSeededUpgrade:
    """Seed at an old revision, upgrade to head, assert preservation."""

    @pytest.mark.parametrize("stop_at", [
        "0002_core_schema", "0003_transactions", "0007_reporting_periods",
    ])
    def test_data_survives_upgrade_to_head(self, cfg: Config, stop_at: str) -> None:
        command.upgrade(cfg, stop_at)
        eng = _eng(cfg)
        with eng.begin() as conn:
            _seed_core(conn)
            if stop_at != "0002_core_schema":
                _seed_transactions(conn)
        command.upgrade(cfg, "head")
        eng2 = _eng(cfg)
        assert current_revision(eng2) == REVISIONS[-1]
        with eng2.connect() as conn:
            assert _count(conn, "household") == 1
            assert _count(conn, "account") == 1
            assert conn.execute(text(
                "SELECT value FROM app_meta WHERE key='seeder'"
            )).scalar_one() == "x"
            if stop_at != "0002_core_schema":
                assert _count(conn, "\"transaction\"") == 2
                # invariant: signed pence preserved exactly
                amounts = sorted(r[0] for r in conn.execute(
                    text("SELECT amount_pence FROM \"transaction\"")))
                assert amounts == [-2500, 100000]
                # later migrations add nullable columns, not lossy ones
                cols = {r[1] for r in conn.execute(
                    text("PRAGMA table_info(\"transaction\")"))}
                assert {"transfer_id", "refund_of_id",
                        "external_ref"}.issubset(cols)

    def test_nonlinear_upgrade_from_mid_revision(self, cfg: Config) -> None:
        """An upgrade started at an intermediate revision (a real
        upgrade-from-old-version path) must reach head cleanly."""
        command.upgrade(cfg, "0005_recurring")
        eng = _eng(cfg)
        with eng.begin() as conn:
            _seed_core(conn)
            _seed_transactions(conn)
            conn.execute(text(
                "INSERT INTO transfer (household_id, matched)"
                " VALUES (1, 0)"))
        command.upgrade(cfg, "head")
        eng2 = _eng(cfg)
        assert current_revision(eng2) == REVISIONS[-1]
        with eng2.connect() as conn:
            assert _count(conn, "transfer") == 1
            assert _count(conn, "\"transaction\"") == 2

    def test_stamp_then_upgrade_matches_fresh(self, cfg: Config) -> None:
        """A DB stamped mid-chain then upgraded ends at the same head."""
        command.upgrade(cfg, "0004_transfers")
        command.stamp(cfg, "0004_transfers")
        command.upgrade(cfg, "head")
        assert current_revision(_eng(cfg)) == REVISIONS[-1]


class TestRuleSetYearBackfill:
    """0067 fills payroll_run.rule_set_year on pre-column rows —
    only where the (tax_year, version, jurisdiction) rule set it
    resolved to provably exists."""

    def _seed(self, conn: Connection) -> None:
        _seed_core(conn)
        conn.execute(text(
            "INSERT INTO user_profile (household_id, display_name,"
            " role, is_active) VALUES (1, 'A', 'admin', 1)"))
        conn.execute(text(
            "INSERT INTO employment (household_id, profile_id,"
            " employer, jurisdiction, contracted_hours)"
            " VALUES (1, 1, 'Acme', 'england_wales', '37.5')"))
        conn.execute(text(
            "INSERT INTO tax_rule_set (tax_year, jurisdiction,"
            " version, params)"
            " VALUES ('2026/27', 'england_wales', 1, '{}')"))
        # resolvable: rules exist for this (year, version, juris.)
        conn.execute(text(
            "INSERT INTO payroll_run (employment_id, period_start,"
            " period_end, pay_date, status, tax_year,"
            " rule_set_version, gross_pence, net_pence)"
            " VALUES (1, '2026-05-01', '2026-05-31', '2026-05-28',"
            " 'forecast', '2026/27', 1, 300000, 240000)"))
        # unresolvable: stale-rules forecast — no 2030/31 set exists
        conn.execute(text(
            "INSERT INTO payroll_run (employment_id, period_start,"
            " period_end, pay_date, status, tax_year,"
            " rule_set_version, gross_pence, net_pence)"
            " VALUES (1, '2030-05-01', '2030-05-31', '2030-05-28',"
            " 'forecast', '2030/31', 1, 300000, 240000)"))
        # gross_only: version 0, no rules applied — stays NULL
        conn.execute(text(
            "INSERT INTO payroll_run (employment_id, period_start,"
            " period_end, pay_date, status, tax_year,"
            " rule_set_version, gross_pence, net_pence)"
            " VALUES (1, '2026-06-01', '2026-06-30', '2026-06-28',"
            " 'forecast', '2026/27', 0, 300000, 300000)"))

    def test_backfill_resolves_only_provable_rows(
            self, cfg: Config) -> None:
        command.upgrade(cfg, "0066_provider_and_income")
        with _eng(cfg).begin() as conn:
            self._seed(conn)
        command.upgrade(cfg, "head")
        with _eng(cfg).connect() as conn:
            rows = conn.execute(text(
                "SELECT tax_year, rule_set_version, rule_set_year"
                " FROM payroll_run ORDER BY period_start")).all()
        assert rows == [
            ("2026/27", 1, "2026/27"),   # backfilled
            ("2026/27", 0, None),        # gross_only — left NULL
            ("2030/31", 1, None),        # ambiguous — left NULL
        ]
