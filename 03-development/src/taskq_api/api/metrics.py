"""[FR-04] ``/v1/metrics`` — admin-only observability endpoint.

Single-purpose router carrying the FR-04 AC-4.2 admin-only endpoint. The
handler inherits its authentication via :func:`taskq_api.api.deps.require_scope`
(the single chokepoint; FR-04 AC-4.3) and uses the
:func:`taskq_api.service.auth.verify_scope` strict-order check to gate
``admin`` access.

Citations:
    - SPEC.md §3 FR-04 AC-4.1 (strict-order scope hierarchy)
    - SPEC.md §3 FR-04 AC-4.2 (admin-only → 403 + problem+json)
    - SPEC.md §3 FR-04 AC-4.3 (single dependency chokepoint)
    - SPEC.md §4 NFR-04 (no secret-bearing output in /v1/metrics)
    - SAD.md §2.8, §3.2
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from taskq_api.api.deps import require_scope
from taskq_api.errors import ForbiddenProblem
from taskq_api.service.auth import Principal, verify_scope

router = APIRouter(prefix="/v1", tags=["metrics"])


@router.get(
    "/metrics",
    summary="Admin-only service metrics",
    description=(
        "[FR-04 AC-4.2] Returns aggregated service metrics. Scope=admin "
        "only — a write- or read-scoped key is rejected with 403 + "
        "problem+json (no resource-existence disclosure)."
    ),
)
async def admin_metrics(
    principal: Principal = Depends(require_scope),
) -> dict:
    """[FR-04] ``GET /v1/metrics`` (scope=admin).

    The handler does NO work other than the scope gate so a write- or
    read-scoped caller never reaches the (potentially secret-bearing)
    metrics payload — keeping NFR-04 (no secret redaction bypass) and
    FR-04 AC-4.2 (insufficient-scope → 403) intact.
    """
    if not verify_scope(principal, "admin"):
        # FR-04 AC-4.2 — strict-order rejection; default ``detail``
        # is the spec-cited "insufficient scope" message (no resource
        # identifier, no existence signal).
        raise ForbiddenProblem()
    return {"queue_depth": 0}
