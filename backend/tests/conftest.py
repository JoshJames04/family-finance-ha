"""Shared fixtures: migrated per-test SQLite session + household."""

from pathlib import Path
from shutil import copyfile

import pytest
from sqlalchemy.orm import sessionmaker

from app.persistence.engine import create_app_engine
from app.persistence.migrate import upgrade_head
from app.persistence.models import Household


@pytest.fixture(scope="session")
def migrated_database(tmp_path_factory):
    # Migrate once per worker, then copy the EMPTY database. Each test still
    # owns a separate database/transaction; migration roundtrips run separately.
    path = tmp_path_factory.mktemp("empty-migrated") / "template.db"
    upgrade_head(f"sqlite:///{path.as_posix()}")
    return path


@pytest.fixture()
def session(tmp_path: Path, migrated_database: Path):
    path = tmp_path / "c.db"
    copyfile(migrated_database, path)
    url = f"sqlite:///{path.as_posix()}"
    factory = sessionmaker(bind=create_app_engine(url))
    with factory() as s:
        yield s


@pytest.fixture()
def household(session):
    hh = Household(name="Test Household")
    session.add(hh)
    session.flush()
    return hh
