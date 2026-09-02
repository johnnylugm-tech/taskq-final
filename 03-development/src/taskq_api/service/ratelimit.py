"""[FR-05] Token-bucket admission control — the rate-limit kernel.

``consume`` is the single entry point the API layer calls: it resolves the
caller's bucket via :class:`taskq_api.repository.rate_repo.RateBucketRepository`
and answers whether the request may proceed. Capacity and refill rate come
from ``TASKQ_RATE_BURST`` / ``TASKQ_RATE_PER_SEC``; the bucket row itself
lives in the database so throttling stays consistent across workers.

Return shape of :func:`consume`:

    - admitted  → ``True``
    - rejected  → ``(False, retry_after_seconds)``

The rejected form carries the refill hint the 429 needs, so the caller can
emit ``Retry-After`` without re-deriving the bucket's rate. Because a
2-tuple is truthy, callers MUST NOT test the result for truthiness —
:func:`check` normalizes both forms into ``(allowed, retry_after_seconds)``
and is what the API layer consumes.

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


def consume(principal: Principal, cost: int = 1):
    """[FR-05 AC-5.1, AC-5.2] Charge ``cost`` tokens to ``principal``'s bucket.

    Args:
        principal: the authenticated caller — its ``key_id`` keys the bucket.
        cost: tokens this request consumes.

    Returns:
        ``True`` when the request is admitted, otherwise
        ``(False, retry_after_seconds)`` — the wait until the bucket holds
        ``cost`` tokens again (AC-5.2's ``Retry-After`` hint).

    Citations: SPEC.md line 116, line 118.
    """
    repository = _bucket_repository
    refill_and_consume = getattr(repository, "refill_and_consume", None)
    if refill_and_consume is None:
        # Reduced bucket surface: only the stored counter is readable, so
        # admission degrades to "does the bucket already hold enough?"
        # with no refill and no retry hint to report.
        return int(repository.get_tokens(principal.key_id)) >= cost
    allowed, retry_after = refill_and_consume(principal.key_id, cost=cost)
    if allowed:
        return True
    return False, retry_after


def check(principal: Principal, cost: int = 1) -> tuple[bool, float]:
    """[FR-05 AC-5.2] :func:`consume` normalized to ``(allowed, retry_after)``.

    The API layer calls this instead of :func:`consume` so the truthy
    2-tuple of a rejection can never be mistaken for an admission.

    Citations: SPEC.md line 118.
    """
    outcome = consume(principal, cost=cost)
    if outcome is True:
        return True, 0.0
    if isinstance(outcome, tuple):
        allowed, retry_after = outcome
        return bool(allowed), float(retry_after)
    # Reduced bucket surface (see :func:`consume`) — report the wait for
    # one token so the 429 still carries a usable hint.
    return False, _one_token_seconds()


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


__all__ = ["check", "consume", "retry_after_seconds"]
