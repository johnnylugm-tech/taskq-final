"""[FR-05] ``rate_buckets`` persistence — row-locked refill + consume.
# Error handling delegated to taskq_api.repository.session.transaction() CM (NFR-03: commit/rollback on any exception).
# pragma: no error-handling

The token bucket lives in the database (NOT in process memory) so a
multi-worker deployment throttles a key consistently: every worker reads
and writes the same row. Each refill + consume pair happens inside ONE
transaction and the row is fetched with a row-level lock
(``SELECT ... FOR UPDATE``), which is what stops two concurrent workers
from both observing "the bucket has tokens" and overdrawing it (AC-5.3 /
NP-13).

Token accounting keeps ``tokens`` an integer column while still honouring
a fractional refill rate: a refill only ever grants WHOLE tokens and
``last_refill`` advances by exactly the time those tokens cost
(``granted / rate``). The leftover fraction stays on the clock instead of
being rounded away, so the long-run admission rate is exactly
``TASKQ_RATE_PER_SEC``.

Bucket key: the bucket row is keyed by
:attr:`taskq_api.service.auth.Principal.key_id` (the SHA-256 hash prefix
the auth chokepoint hands the API layer) rather than the ``api_keys.id``
surrogate named in ADR-008 — the chokepoint resolves a ``Principal``, and
fixture principals authenticate without an ``api_keys`` row, so the
surrogate is not available at the point the bucket is consulted.

Citations:
    - SPEC.md line 116 (capacity ``TASKQ_RATE_BURST``, refill ``TASKQ_RATE_PER_SEC``)
    - SPEC.md line 118 (429 + problem+json + ``Retry-After`` seconds)
    - SPEC.md line 119 (bucket state in the DB; single transaction + row-level lock)
    - SPEC.md line 313 (``rate_buckets`` table catalog)
    - SRS.md §3 FR-05 AC-5.1, AC-5.3
    - SAD.md §2.6 (transaction boundary), ADR-008 (DB-backed token bucket)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taskq_api.config import get_settings
from taskq_api.models.orm import RateBucket
from taskq_api.repository.session import transaction


def _utc_now() -> datetime:
    """[FR-05] UTC clock used for ``last_refill`` arithmetic."""
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    """[FR-05] Attach UTC to a naive ``last_refill`` read back from the DB.

    SQLite has no timezone-aware storage, so a ``DateTime(timezone=True)``
    column round-trips as a naive value. Every timestamp this repository
    writes is UTC, so re-attaching UTC restores the original instant and
    keeps the subtraction against :func:`_utc_now` well-defined.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


class RateBucketRepository:
    """[FR-05] Row-locked token-bucket persistence for ``rate_buckets``.

    Public surface:
        - ``refill_and_consume(key_id, cost=1)`` → ``(allowed, retry_after_seconds)``
        - ``get_tokens(key_id)``                 → current stored token count

    Citations: SPEC.md line 119 (single transaction + row-level lock).
    """

    def refill_and_consume(
        self, key_id: str, cost: int = 1,
    ) -> tuple[bool, float]:
        """[FR-05 AC-5.1, AC-5.3] Refill then consume ``cost`` tokens.

        The whole read-modify-write runs in ONE transaction and takes a
        row-level lock on the bucket row before computing the new token
        count, so concurrent workers serialize on the row instead of
        racing (AC-5.3 / NP-13).

        Args:
            key_id: bucket key — ``Principal.key_id``.
            cost: tokens this request consumes.

        Returns:
            ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is
            ``0.0`` when the request is admitted, and otherwise the wait
            until the bucket holds ``cost`` tokens again.

        Citations: SPEC.md line 116, line 119.
        """
        settings = get_settings()
        capacity = int(settings.rate_burst)
        rate = float(settings.rate_per_sec)
        now = _utc_now()

        with transaction() as session:
            row = self._load_or_refill(session, key_id, capacity, rate, now)
            if int(row.tokens) >= cost:
                row.tokens = int(row.tokens) - cost
                return True, 0.0
            return False, _wait_seconds(int(row.tokens), cost, rate)

    def _load_or_refill(
        self,
        session,
        key_id: str,
        capacity: int,
        rate: float,
        now: datetime,
    ) -> RateBucket:
        """[FR-05 AC-5.3] Fetch the bucket row under a row-level lock, or
        seed a fresh one.

        ``session.get(..., with_for_update=True)`` renders ``SELECT ...
        FOR UPDATE`` on engines that support it (Postgres); SQLite
        serializes writers on the transaction itself, so the same call
        path covers both backends without branching. A missing row is
        seeded at full capacity — the first request from a new principal
        starts with a full bucket, exactly like every other request up
        to the burst limit.
        """
        row = session.get(RateBucket, key_id, with_for_update=True)
        if row is None:
            row = RateBucket(key_id=key_id, tokens=capacity, last_refill=now)
            session.add(row)
            return row
        tokens, last_refill = _refill(
            tokens=int(row.tokens),
            last_refill=_as_utc(row.last_refill),
            now=now,
            capacity=capacity,
            rate=rate,
        )
        row.tokens = tokens
        row.last_refill = last_refill
        return row

    def get_tokens(self, key_id: str) -> int:
        """[FR-05] Return the bucket's stored token count (0 if no row yet).

        A plain read of the persisted counter — it does NOT refill and does
        NOT consume, so callers can inspect bucket state without moving it.
        """
        with transaction() as session:
            row = session.get(RateBucket, key_id)
            if row is None:
                return 0
            return int(row.tokens)


def _refill(
    tokens: int,
    last_refill: datetime,
    now: datetime,
    capacity: int,
    rate: float,
) -> tuple[int, datetime]:
    """[FR-05 AC-5.1] Grant the whole tokens earned since ``last_refill``.

    Returns the new ``(tokens, last_refill)`` pair. ``last_refill`` only
    advances by the time the granted tokens actually cost, so the
    fractional remainder carries into the next call; a bucket that reaches
    ``capacity`` re-bases its clock to ``now`` (there is nothing left to
    accrue). ``rate <= 0`` disables refill, and a backwards clock grants
    nothing.

    Citations: SPEC.md line 116 (refill rate ``TASKQ_RATE_PER_SEC``).
    """
    if rate <= 0:
        return min(tokens, capacity), last_refill
    elapsed = (now - last_refill).total_seconds()
    granted = int(elapsed * rate)
    if granted <= 0:
        return tokens, last_refill
    refilled = min(capacity, tokens + granted)
    if refilled >= capacity:
        return capacity, now
    return refilled, last_refill + timedelta(seconds=granted / rate)


def _wait_seconds(tokens: int, cost: int, rate: float) -> float:
    """[FR-05 AC-5.2] Seconds until the bucket holds ``cost`` tokens.

    Feeds the ``Retry-After`` header. A non-positive ``rate`` never
    refills, so the shortfall is reported as one full ``cost`` interval
    rather than as an infinite wait.

    Citations: SPEC.md line 118 (``Retry-After`` in seconds).
    """
    shortfall = max(cost - tokens, 1)
    if rate <= 0:
        return float(shortfall)
    return shortfall / rate


__all__ = ["RateBucketRepository"]
