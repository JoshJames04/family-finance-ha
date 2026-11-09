"""Session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_session_factory: sessionmaker[Session] | None = None


def init_session_factory(engine: Engine) -> sessionmaker[Session]:
    global _session_factory
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_session() -> Iterator[Session]:
    if _session_factory is None:
        raise RuntimeError("persistence not initialised")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        for path in session.info.pop("payslip_paths", set()):
            path.unlink(missing_ok=True)
        raise
    finally:
        session.close()
