"""[FR-09] Liveness + readiness endpoints.

``/healthz`` is an always-200 liveness probe that answers ``{"status": "ok"}``
and carries NO auth dependency (AC-9.1).
``/readyz`` answers 200 iff the DB is reachable AND ``alembic current``
equals ``alembic head``; otherwise 503 with a body that names WHICH
check failed (AC-9.2 + AC-9.4).

Both routes carry no auth and no rate limit (per SPEC.md §3 FR-03
AC-3.5 / FR-05 AC-5.4) — the probe routes never depend on
``require_scope``.

Citations:
    - SPEC.md §3 FR-09 (AC-9.1, AC-9.2, AC-9.3, AC-9.4)
    - SPEC.md line 158 (AC-9.4 fail-closed on migration drift)
    - SPEC.md §3 FR-03 AC-3.5 / FR-05 AC-5.4 (probe routes anonymous)
    - SAD.md §2.9, §3.5
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from taskq_api.service import health as _health

router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    summary="Liveness probe",
    description=(
        "[FR-09 AC-9.1] Always-200 liveness probe — answers "
        "``{\"status\": \"ok\"}`` with no auth dependency so a load "
        "balancer can target it without bespoke discovery."
    ),
)
async def healthz() -> dict:
    """[FR-09 AC-9.1] Always-200 liveness probe."""
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness probe",
    description=(
        "[FR-09 AC-9.2, AC-9.4] 200 iff the DB is reachable AND the "
        "alembic revision is at head; otherwise 503 with a body that "
        "names WHICH check failed."
    ),
)
async def readyz() -> JSONResponse:
    """[FR-09 AC-9.2, AC-9.4] 200 iff DB reachable AND migrations at head.

    Fails closed (503) when EITHER:

      - The database engine cannot answer ``SELECT 1`` (AC-9.2 — body
        detail key ``"database unavailable"``).
      - The alembic current revision differs from head (AC-9.4 — body
        detail key ``"migration not at head"``). This is the canonical
        "deployment forgot to run migrations" invariant required by
        SPEC.md line 158.

    The body always names the failing check so an operator can act on
    the probe result without parsing logs (NFR-03 fail-closed).

    Both checks delegate to :mod:`taskq_api.service.health` — the
    handler never reaches into the repository directly (NFR-06
    layering). ``is_migration_at_head()`` encapsulates the
    ``current == head`` invariant (including the ``None`` →
    "no migrations applied" case) so the handler stays a flat
    list of named checks.
    """
    if not _health.is_database_ready():
        return JSONResponse(
            status_code=503,
            content={
                "status": "not-ready",
                "detail": "database unavailable",
            },
        )
    if not _health.is_migration_at_head():
        return JSONResponse(
            status_code=503,
            content={
                "status": "not-ready",
                "detail": "migration not at head",
            },
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
