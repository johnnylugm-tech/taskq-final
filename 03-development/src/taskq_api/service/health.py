"""[FR-09] Service-layer health checks — L1 (service).

The readiness probe asks the service whether the database is reachable
and whether the alembic revision is at head; the service forwards the
call down to the repository so the HTTP handler in ``api/health.py``
never crosses layers.

Citations:
    - SPEC.md §3 FR-09 (liveness / readiness)
    - SPEC.md line 158 (AC-9.4 fail-closed on migration drift)
    - SAD.md §2.7 (layer boundaries)
"""

from __future__ import annotations

from taskq_api.repository import session as _session

__all__ = [
    "alembic_head",
    "current_alembic_revision",
    "is_database_ready",
    "is_migration_at_head",
]


def is_database_ready() -> bool:
    """[FR-09 AC-9.2] Return ``True`` iff the DB engine answers ``SELECT 1``.

    Public service-layer entry point for the readiness probe; the handler
    in ``api/health.py`` calls this rather than reaching into the
    repository directly, preserving the ``api > service > repository``
    layer order (NFR-06).
    """
    return _session.ping()


def current_alembic_revision() -> str | None:
    """[FR-09 AC-9.4] Return the alembic revision currently stamped in the DB.

    Reads the ``alembic_version`` table; returns ``None`` when the table
    is missing (i.e. migrations have not been applied at all) so the
    readiness probe can fail closed.

    Citations: SPEC.md line 158 (AC-9.4 fail-closed).
    """
    return _session.current_alembic_revision()


def alembic_head() -> str:
    """[FR-09 AC-9.4] Return the alembic head revision expected at runtime.

    Discovered by scanning the FR-07 ``migrations/versions`` directory so
    the answer stays in sync as new revisions land — the deployment's
    "did I forget to run migrations?" check should not be hard-coded.
    """
    return _session.alembic_head()


def is_migration_at_head() -> bool:
    """[FR-09 AC-9.4] Return ``True`` iff ``alembic current == alembic head``.

    The deployment forgot to run ``alembic upgrade head`` invariant —
    SPEC.md line 158: "deployment forgot to run migration MUST fail
    closed". A ``None`` current revision (no ``alembic_version`` row
    at all) is treated as drift so a fresh DB that never had migrations
    applied also fails closed.
    """
    current = current_alembic_revision()
    if current is None:
        return False
    return current == alembic_head()
