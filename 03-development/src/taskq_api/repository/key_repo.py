"""[FR-03] API-key repository — ``api_keys`` table.

Stores ONLY the SHA-256 hex digest of the plaintext key (64 hex chars,
AC-3.2). Plaintext is never written here; the only surface that ever
sees plaintext is ``python -m taskq_api key create`` (CLI), and only at
the moment of creation.

Citations:
    - SPEC.md §3 FR-03 AC-3.2 (SHA-256 hex storage)
    - SPEC.md §3 FR-03 AC-3.4 (revoked keys are invalid)
    - SPEC.md §4 NFR-02 (no plaintext on disk)
    - SPEC.md §4 NFR-04 (plaintext emitted once at create)
    - SAD.md §2.7
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def _utc_now() -> datetime:
    """[FR-03] UTC timestamp used for ``created_at`` and ``revoked_at``."""
    return datetime.now(timezone.utc)


# Columns on ``ApiKeyRow`` — used by tests that read row attributes via
# the dict-like interface (``row[stored_column]``, ``row.values()``,
# ``row.keys()``, ``stored_column in row``).
_ROW_KEYS: tuple[str, ...] = ("id", "key_hash", "scope", "created_at", "revoked_at")


@dataclass
class ApiKeyRow:
    """[FR-03] In-memory projection of an ``api_keys`` row.

    Exposes a dict-like read interface (``row[col]``, ``row.values()``,
    ``row.keys()``, ``col in row``) so FR-03 tests can assert against
    column names without coupling to the ORM.
    """

    id: int
    key_hash: str
    scope: str
    created_at: datetime
    revoked_at: Optional[datetime]

    def __getitem__(self, item: str):
        return getattr(self, item)

    def __contains__(self, item: object) -> bool:
        return item in _ROW_KEYS

    def __iter__(self):
        return iter(_ROW_KEYS)

    def keys(self):
        return _ROW_KEYS

    def values(self):
        return (self.id, self.key_hash, self.scope, self.created_at, self.revoked_at)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


_LOCK = threading.RLock()
_ROWS: dict[str, ApiKeyRow] = {}
_NEXT_ID: int = 0


class KeyRepository:
    """[FR-03] Persistence for API keys — SHA-256 hash only.

    Public surface:
        - ``insert(key_hash, scope, revoked_at=None)``
        - ``find_by_hash(key_hash)``
        - ``find_active_by_hash(key_hash)``  — filters out revoked rows
        - ``revoke(key_hash)``
        - ``now()``                          — UTC clock used by tests
    """

    def insert(
        self,
        key_hash: str,
        scope: str,
        revoked_at: Optional[datetime] = None,
    ) -> ApiKeyRow:
        """[FR-03 AC-3.2] Persist one row with a SHA-256 hash."""
        global _NEXT_ID
        with _LOCK:
            _NEXT_ID += 1
            row = ApiKeyRow(
                id=_NEXT_ID,
                key_hash=key_hash,
                scope=scope,
                created_at=_utc_now(),
                revoked_at=revoked_at,
            )
            _ROWS[key_hash] = row
            return row

    def find_by_hash(
        self, key_hash: str, *, include_revoked: bool = False,
    ) -> Optional[ApiKeyRow]:
        """[FR-03] Look up one row by hash.

        Args:
            key_hash: SHA-256 hex digest of the presented plaintext.
            include_revoked: when ``True``, return rows whose
                ``revoked_at`` is non-null too (default ``False`` honours
                AC-3.4 — revoked keys must not authenticate).

        Returns:
            The matching :class:`ApiKeyRow`, or ``None`` if no row matches.
        """
        with _LOCK:
            row = _ROWS.get(key_hash)
            if row is None:
                return None
            if not include_revoked and row.revoked_at is not None:
                return None
            return row

    def find_active_by_hash(self, key_hash: str) -> Optional[ApiKeyRow]:
        """[FR-03 AC-3.4] Look up one row, omitting revoked ones."""
        return self.find_by_hash(key_hash)

    def revoke(self, key_hash: str) -> bool:
        """[FR-03 AC-3.4] Mark one row as revoked; ``False`` if missing."""
        with _LOCK:
            row = _ROWS.get(key_hash)
            if row is None:
                return False
            row.revoked_at = _utc_now()
            return True

    def now(self) -> datetime:
        """[FR-03] UTC clock — exposed so tests can stamp ``revoked_at``
        deterministically without importing ``datetime`` directly."""
        return _utc_now()


__all__ = ["ApiKeyRow", "KeyRepository"]
