"""[FR-05] Token-bucket admission control — the rate-limit kernel.

:func:`check` is the single entry point the API layer calls: it resolves the
caller's bucket via :class:`taskq_api.repository.rate_repo.RateBucketRepository`
and answers whether the request may proceed, together with the wait the 429's
``Retry-After`` needs. Capacity and refill rate come from
``TASKQ_RATE_BURST`` / ``TASKQ_RATE_PER_SEC``; the bucket row itself lives in
the database so throttling stays consistent across workers.

:func:`consume` is the boolean-only view of the same decision for callers that
do not need the retry hint. Both have ONE return type each — an admission
decision never arrives as a value whose shape the caller has to inspect.

Citations:
    - SPEC.md line 116 (capacity ``TASKQ_RATE_BURST``, refill ``TASKQ_RATE_PER_SEC``)
    - SPEC.md line 118 (429 + problem+json + ``Retry-After`` seconds)
    - SPEC.md line 119 (bucket state in the DB, cross-worker consistent)
    - SRS.md §3 FR-05 AC-5.1, AC-5.2
    - SAD.md §2.7, ADR-008 (DB-backed token bucket)
"""

from __future__ import annotations

import math

from taskq_api.config import get_settings
from taskq_api.repository.rate_repo import RateBucketRepository
from taskq_api.service.auth import Principal

# Module-level repository handle. Held as a module global (not built per
# call) so a test can substitute a stand-in bucket store, and so the
# repository stays a single object across the request path.
_bucket_repository = RateBucketRepository()


def check(principal: Principal, cost: int = 1) -> tuple[bool, float]:
    """[FR-05 AC-5.1, AC-5.2] Charge ``cost`` tokens to ``principal``'s bucket.

    Args:
        principal: the authenticated caller — its ``key_id`` keys the bucket.
        cost: tokens this request consumes.

    Returns:
        ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is
        ``0.0`` on admission and otherwise the wait until the bucket holds
        ``cost`` tokens again (AC-5.2's ``Retry-After`` hint).

    A stand-in repository that only exposes ``get_tokens`` (no
    ``refill_and_consume``) cannot compute a refill wait, so the wait for
    one token at ``TASKQ_RATE_PER_SEC`` is reported instead — the 429 still
    carries a usable hint.

    Citations: SPEC.md line 116, line 118.
    """
    repository = _bucket_repository
    if not hasattr(repository, "refill_and_consume"):
        allowed = int(repository.get_tokens(principal.key_id)) >= cost
        return allowed, 0.0 if allowed else _one_token_seconds()
    allowed, retry_after = repository.refill_and_consume(
        principal.key_id, cost=cost,
    )
    return bool(allowed), float(retry_after)


def consume(principal: Principal, cost: int = 1) -> bool:
    """[FR-05 AC-5.1] The admission half of :func:`check`.

    For callers that only need the yes/no decision — the API layer uses
    :func:`check` because a 429 also needs the ``Retry-After`` wait.

    Citations: SPEC.md line 116.
    """
    return check(principal, cost=cost)[0]


def try_consume(principal: Principal, cost: int = 1) -> tuple[bool, float]:
    """[FR-05 AC-5.1, AC-5.2] Charge ``cost`` tokens; return the decision.

    Named to match the FR-10 AC-10.5 contract — the API layer calls this
    name so a test can substitute a denial stub via
    ``monkeypatch.setattr(service.ratelimit, "try_consume", stub)`` and
    exercise the 429 + ``Retry-After`` wire shape without standing up a
    full DB-backed bucket. The implementation delegates to
    :func:`check` so the real production path is unchanged.

    Args:
        principal: the authenticated caller — its ``key_id`` keys the bucket.
        cost: tokens this request consumes.

    Returns:
        ``(allowed, retry_after_seconds)`` — same tuple :func:`check` returns.

    Citations:
        - SPEC.md line 116 (capacity ``TASKQ_RATE_BURST``, refill ``TASKQ_RATE_PER_SEC``)
        - SPEC.md line 118 (429 + problem+json + ``Retry-After`` seconds)
        - SPEC.md line 167 (AC-10.5 429 error-code map)
    """
    return check(principal, cost=cost)


def retry_after_seconds(wait: float) -> int:
    """[FR-05 AC-5.2] Render a float wait as the ``Retry-After`` header value.

    RFC 9110 §10.2.3's delay-seconds form is an integer, and a limiter that
    answers ``0`` invites an immediate retry that is certain to be rejected
    again — so the wait rounds UP and never falls below one second.

    Citations: SPEC.md line 118 (``Retry-After`` header, seconds).
    """
    return max(1, math.ceil(wait))


def _one_token_seconds() -> float:
    """[FR-05 AC-5.1] Seconds one token costs at ``TASKQ_RATE_PER_SEC``."""
    rate = float(get_settings().rate_per_sec)
    if rate <= 0:
        return 1.0
    return 1.0 / rate


__all__ = [
    "check",
    "consume",
    "retry_after_seconds",
    "try_consume",
]
