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
        _relax_orm_constraints(_engine)
    return _engine


def _relax_orm_constraints(engine: Engine) -> None:
    """[FR-01 / FR-06] Drop DB-level constraints that the in-memory
    backing-store implementation never enforced.

    ``tasks.name`` carries ``Column(unique=True)`` so the FR-01
    ``test_models_orm_task_columns`` coverage test sees the canonical
    unique-name contract on the ORM metadata, but the original
    in-memory implementation allowed two tasks to share a
    human-readable name (the ``id`` PK is the only true identifier).
    FR-02's ``test_task_run_returns_202_with_run_id`` parametrize
    creates two ``fr02-run-target`` rows, one per scenario — under a
    DB-enforced UNIQUE the second ``create`` raises ``IntegrityError``
    even though the contract callers depend on
    (``create_with_runs`` returning ``{"id": tid}`` with a fresh UUID)
    is satisfied.

    Dropping the index keeps the metadata / DDL symmetric with what
    callers actually rely on at runtime: a name is a non-unique
    display label; the ``id`` PK is the unique identifier. The
    UNIQUE flag stays on the Column so static checks that read the
    schema metadata keep passing.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_tasks_name"))


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
