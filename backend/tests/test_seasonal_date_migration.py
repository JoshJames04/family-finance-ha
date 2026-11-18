"""Existing occasions retain unknown dates through an additive migration."""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.persistence.migrate import alembic_config


def test_existing_rows_remain_unchanged_and_single_head_roundtrips(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0070_transfer_confirmation_history")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO household (id,name) VALUES (1,'Synthetic')"))
        conn.execute(text("INSERT INTO seasonal_event "
                          "(id,household_id,year,type,name,budget_pence) "
                          "VALUES (1,1,2026,'other','Synthetic occasion',10000)"))
    command.upgrade(cfg, "0071_seasonal_event_date")
    with engine.connect() as conn:
        row = conn.execute(text("SELECT name,budget_pence,event_date FROM seasonal_event")).one()
        assert tuple(row) == ("Synthetic occasion", 10000, None)
    command.downgrade(cfg, "0070_transfer_confirmation_history")
    columns = {col["name"] for col in inspect(engine).get_columns("seasonal_event")}
    assert "event_date" not in columns
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT event_date FROM seasonal_event")).scalar() is None
    engine.dispose()
