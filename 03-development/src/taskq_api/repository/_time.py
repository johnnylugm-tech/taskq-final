"""[FR-03, FR-05, FR-06] UTC clock helpers shared across repositories.

Centralises the two tiny datetime helpers every repository needs:

* :func:`utc_now` — UTC clock used for ``created_at`` /
  ``revoked_at`` / ``last_refill`` writes.
* :func:`as_utc` — re-attach UTC to a naive timestamp read back
  from SQLite, which has no timezone-aware storage.

Previously each repository (``key_repo`` / ``rate_repo`` /
``task_repo``) defined its own copy; this module is the single
source of truth so the FR-citation stays in one place.

Citations:
    - SPEC.md §4 NFR-03 (consistent timestamps)
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC instant (timezone-aware)."""
    return datetime.now(timezone.utc)


def as_utc(moment: datetime) -> datetime:
    """Re-attach UTC to a naive ``datetime`` read back from SQLite.

    SQLite has no timezone-aware storage, so ``DateTime(timezone=True)``
    columns round-trip as naive values. Every timestamp this layer writes
    is UTC, so re-attaching UTC restores the original instant.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
