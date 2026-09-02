"""[FR-06, NFR-03] Transaction boundary / pool / ping — L2.

Owns the SQLAlchemy ``engine`` and the ``transaction()`` context-manager.
Every repository call must enter one.

Citations:
    - SPEC.md §3 FR-06 AC-6.2 (commit/rollback via CM)
    - SPEC.md §4 NFR-03 (no bare except; rollback always)
    - SAD.md §2.6
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import get_settings
from taskq_api.models.orm import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.db_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args=connect_args,
        future=True,
    )


def get_engine() -> Engine:
    """Return the cached engine, building tables on first access."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _build_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, future=True)
        Base.metadata.create_all(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the cached session factory."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def transaction() -> Iterator[Session]:
    """[FR-06, NFR-03] Commit on clean exit, rollback on any exception."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    """[FR-09] Return ``True`` iff the engine can answer ``SELECT 1``."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
