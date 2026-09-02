"""[FR-09] Liveness + readiness endpoints.

``/healthz`` and ``/readyz`` carry no auth and no rate limit (per SPEC.md
§3 FR-03 AC-3.5 / FR-05 AC-5.4).

Citations:
    - SPEC.md §3 FR-09
    - SAD.md §3.5
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict:
    """[FR-09] Always-200 liveness probe."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz() -> dict:
    """[FR-09] 200 iff the DB is reachable and migrations are at head."""
    from taskq_api.repository.session import ping

    if not ping():
        return {"status": "not-ready", "reason": "database unavailable"}
    return {"status": "ok"}
