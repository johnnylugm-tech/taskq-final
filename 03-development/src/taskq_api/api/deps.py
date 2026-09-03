"""[FR-03, FR-04] Single FastAPI dependency for authentication.

Every ``/v1/*`` route depends on :func:`require_scope` — the SINGLE
chokepoint that authenticates the caller's ``X-API-Key`` and resolves
it to a :class:`taskq_api.service.auth.Principal` (FR-04 AC-4.3). Each
handler then advertises its required scope by calling
:func:`taskq_api.service.auth.verify_scope` (or the matching
``_require_scope`` helper in ``api/tasks``), so the per-route scope
constant stays declarative while the auth path stays centralised.

The default ``403`` raised downstream is ``ForbiddenProblem`` whose body
is the generic ``"insufficient scope"`` message — never the task id or
any resource-existence signal (FR-04 AC-4.2, NFR-02).

Citations:
    - SPEC.md §3 FR-03 AC-3.1 (authenticate via X-API-Key)
    - SPEC.md §3 FR-04 AC-4.2 (403 must not disclose existence)
    - SPEC.md §3 FR-04 AC-4.3 (single dependency chokepoint)
    - SAD.md §2.8, §3.2
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, Request

from taskq_api.errors import RateLimitedProblem, UnauthenticatedProblem
from taskq_api.service import ratelimit
from taskq_api.service.auth import Principal, verify_key


def _enforce_rate_limit(principal: Principal) -> None:
    """[FR-05 AC-5.1, AC-5.2] Charge one token to the caller's bucket.

    Raises :class:`RateLimitedProblem` (→ 429 + problem+json +
    ``Retry-After``) when the per-key bucket is empty. Only ``/v1/*``
    routes reach this function — they are the routes that depend on
    :func:`require_scope`; ``/healthz`` and ``/readyz`` declare no
    dependency and are therefore exempt (AC-5.4).

    The admission call is dispatched through
    :func:`taskq_api.service.ratelimit.try_consume` (rather than
    :func:`check`) so a test can substitute a denial stub via
    ``monkeypatch.setattr(service.ratelimit, "try_consume", stub)``
    and exercise the FR-10 AC-10.5 429 wire shape end-to-end.

    Citations:
        - SPEC.md line 116, line 118, line 120 (FR-05)
        - SPEC.md line 167 (FR-10 AC-10.5 rate-limited error code)
    """
    allowed, wait = ratelimit.try_consume(principal)
    if not allowed:
        raise RateLimitedProblem(
            retry_after=ratelimit.retry_after_seconds(wait),
        )


async def require_auth(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """[FR-03 AC-3.1, FR-05 AC-5.1, T-13] Authenticate, then charge the bucket.

    Raises :class:`UnauthenticatedProblem` (→ 401) for missing / unknown
    keys. Per-route scope authorization is done inline in the handler
    via :func:`taskq_api.service.auth.verify_scope` so each route can
    declare its own required scope while the response body stays
    generic — ``ForbiddenProblem``'s default detail is
    ``"insufficient scope"``, never a resource identifier (NFR-02).

    Rate limiting runs as its own layer inside this chokepoint, AFTER the
    key resolves to a :class:`Principal` (the bucket is per principal, so
    it cannot be charged before the caller is known) and BEFORE the
    handler's scope check — an over-limit caller never reaches the handler
    (FR-05 AC-5.1 / AC-5.2).

    The resolved :class:`Principal` is also stashed on
    ``request.state.principal`` so the FR-10 audit-log middleware in
    :mod:`taskq_api.app` can re-emit a record carrying the principal's
    ``key_id`` alongside the correlation_id — closing the T-13
    repudiation gap (privileged actions become traceable to a caller).
    """
    principal = verify_key(x_api_key)
    if principal is None:
        raise UnauthenticatedProblem()
    request.state.principal = principal
    _enforce_rate_limit(principal)
    return principal


# Public alias: ``require_scope`` is the FR-04 AC-4.3 chokepoint name and
# the one every ``/v1/*`` route's ``Depends(...)`` references. It is the
# same callable as :func:`require_auth` so the resolved dependency graph
# stays a single identity across every handler.
require_scope = require_auth
