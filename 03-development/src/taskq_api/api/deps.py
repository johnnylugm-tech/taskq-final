"""[FR-03, FR-04] Single FastAPI dependency for auth + scope.

Every ``/v1/*`` route depends on :func:`require_scope` — the order is
**authenticate → authorize → rate-limit → resource lookup**, so a 403
never leaks the existence of the resource (NFR-02, FR-04 R4).

The dependency only authenticates; per-route scope checks happen inline in
the handler so a single chokepoint (FR-04 AC-4.3) stays intact while each
route advertises its required scope.

Citations:
    - SPEC.md §3 FR-03 AC-3.1
    - SPEC.md §3 FR-04 AC-4.2 (403 must not disclose existence)
    - SAD.md §2.8, §3.2
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header

from taskq_api.errors import UnauthenticatedProblem
from taskq_api.service.auth import Principal, verify_key


async def require_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """[FR-03] Authenticate the request — return a :class:`Principal`.

    Raises :class:`UnauthenticatedProblem` (→ 401) for missing / unknown
    keys. Per-route scope authorization is done inline in the handler
    (see :mod:`taskq_api.api.tasks`) so each route can declare its own
    scope constant and the response body stays generic (NFR-02).
    """
    principal = verify_key(x_api_key)
    if principal is None:
        raise UnauthenticatedProblem()
    return principal


# Backwards-compatible alias kept so existing ``Depends(require_scope)``
# call-sites continue to work. The new name ``require_auth`` is preferred
# because scope enforcement now lives in the handler.
require_scope = require_auth
