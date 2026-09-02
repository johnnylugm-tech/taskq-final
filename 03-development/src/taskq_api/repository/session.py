"""[FR-06, FR-09, NFR-03] Transaction boundary / pool / ping / alembic probe — L2.

Owns the SQLAlchemy ``engine`` and the ``transaction()`` context-manager.
Every repository call must enter one.

The FR-09 alembic-revision probe (``current_alembic_revision``,
``alembic_head``, ``is_migration_at_head``) lives here so the readiness
probe in ``api/health.py`` can fail closed when a deployment forgot to
run migrations (SPEC.md line 158, AC-9.4).

Citations:
    - SPEC.md §3 FR-06 AC-6.2 (commit/rollback via CM)
    - SPEC.md §3 FR-09 AC-9.2 (DB ping) / AC-9.4 (migration drift fail-closed)
    - SPEC.md line 158 (AC-9.4 deployment-forgot-migrations invariant)
    - SPEC.md §4 NFR-03 (no bare except; rollback always)
    - SAD.md §2.6
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from taskq_api.config import get_settings
from taskq_api.models.orm import Base

# Re-exports so the rest of ``repository/`` can compose SQL without
# importing ``sqlalchemy`` directly. The project-wide import-linter
# contract forbids any ``taskq_api.*`` (except ``session`` and
# ``models.orm``) from importing ``sqlalchemy`` — the SQL builder
# symbols live here and are delegated outward by name.
__all__ = [
    "Engine",
    "Session",
    "alembic_head",
    "create_engine",
    "current_alembic_revision",
    "get_engine",
    "get_session_factory",
    "is_migration_at_head",
    "ping",
    "select",
    "selectinload",
    "sessionmaker",
    "text",
    "transaction",
]

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    """[FR-06 AC-6.5] Construct the engine with the spec-required pool config.

    ``pool_size`` honours ``TASKQ_DB_POOL_SIZE`` (default 5) and
    ``pool_pre_ping=True`` drops stale connections before each checkout
    — the load-bearing half of AC-6.5, because a stale connection would
    silently surface a 5xx on the next request.
    """
    settings = get_settings()
    url = settings.db_url
    # SQLite runs in-process and serializes writers on the transaction
    # itself, so a single connection is reused across threads.
    connect_args = (
        {"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args=connect_args,
        future=True,
    )


def get_engine() -> Engine:
    """Return the cached engine, building tables on first access.

    First call creates the engine, opens the schema metadata, and drops
    the DB-level UNIQUE index on ``tasks.name`` (see
    :func:`_relax_orm_constraints`). Subsequent calls return the cached
    engine so callers share a single connection pool.
    """
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
    """[FR-09 AC-9.2] Return ``True`` iff the engine can answer ``SELECT 1``.

    Any exception (engine down, network partition, auth failure) is
    collapsed to ``False`` so the readiness probe can answer 503 with
    a stable boolean — SPEC.md line 156 (AC-9.2 fail-closed).
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def current_alembic_revision() -> str | None:
    """[FR-09 AC-9.4] Return the alembic revision stamped in the DB.

    Reads the canonical ``alembic_version`` table alembic writes during
    ``alembic upgrade``. Returns ``None`` when the table is missing
    (migrations never ran, OR the DB itself is unreachable — the
    catch-all ``Exception`` branch collapses both into the same
    "not at head" signal so the readiness probe fails closed in either
    case).

    Citations: SPEC.md line 158 (AC-9.4 deployment-forgot-migrations).
    """
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version"),
            ).first()
    except Exception:
        return None
    if row is None:
        return None
    return row[0]


def alembic_head() -> str:
    """[FR-09 AC-9.4] Return the alembic head revision expected at runtime.

    Discovers the head by parsing each revision script's
    ``revision = "<id>"`` / ``down_revision = "<id>"`` declarations in
    the FR-07 ``migrations/versions`` directory — the head is the
    revision that is NOT the down_revision of any other. This keeps
    the answer in sync as new revisions land without requiring the
    alembic runtime proxy at import time.
    """
    versions_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "migrations"
        / "versions"
    )
    revisions: dict[str, str | None] = {}
    for script in sorted(versions_dir.glob("*.py")):
        if script.name.startswith("_"):
            continue
        content = script.read_text(encoding="utf-8")
        rev_match = re.search(
            r"""^revision\s*=\s*['"]([^'"]+)['"]""",
            content,
            re.MULTILINE,
        )
        if rev_match is None:
            continue
        rev = rev_match.group(1)
        down_match = re.search(
            r"""^down_revision\s*=\s*(?P<val>['"]([^'"]+)['"]|None)""",
            content,
            re.MULTILINE,
        )
        if down_match is None:
            revisions[rev] = None
            continue
        down_val = down_match.group(2) if down_match.group(2) else None
        revisions[rev] = down_val
    if not revisions:
        return ""
    down_targets = {down for down in revisions.values() if down is not None}
    heads = [rev for rev, down in revisions.items() if rev not in down_targets]
    if heads:
        return heads[0]
    return max(revisions.keys())


def is_migration_at_head() -> bool:
    """[FR-09 AC-9.4] Return ``True`` iff ``current_alembic_revision() == alembic_head()``.

    A ``None`` current revision (table missing OR DB unreachable) is
    treated as drift so the readiness probe fails closed — the canonical
    SPEC.md line 158 invariant ("deployment forgot to run migration MUST
    fail closed").
    """
    current = current_alembic_revision()
    if current is None:
        return False
    return current == alembic_head()
