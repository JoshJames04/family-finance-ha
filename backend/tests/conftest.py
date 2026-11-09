"""Shared fixtures: migrated per-test SQLite session + household."""

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.persistence.engine import create_app_engine
from app.persistence.migrate import upgrade_head
from app.persistence.models import Household


@pytest.fixture()
def session(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    upgrade_head(url)
    factory = sessionmaker(bind=create_app_engine(url))
    with factory() as s:
        yield s


@pytest.fixture()
def household(session):
    hh = Household(name="Test Household")
    session.add(hh)
    session.flush()
    return hh
