"""[FR-03] API-key persistence — L2.

DB-backed ``api_keys`` table. Stores ONLY the SHA-256 hex digest of the
plaintext key (never the plaintext); the ``revoked_at`` column lets
operators retire a key without losing the audit trail.

The repository is the single owner of the ``api_keys`` row shape —
``service.auth`` reads it through ``find_active_by_hash`` so revoked
keys are transparently rejected (AC-3.4).

Citations:
    - SPEC.md §3 FR-03 AC-3.2 (SHA-256 hash, never plaintext)
    - SPEC.md §3 FR-03 AC-3.4 (revoked_at filters inactive keys)
    - SAD.md §2.6, §3.4
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


_LOCK = threading.RLock()
_KEYS: dict[str, "ApiKeyRow"] = {}


@dataclass
class ApiKeyRow:
    """[FR-03] One ``api_keys`` row.

    The ``key_hash`` column is a 64-char lowercase hex digest of the
    plaintext key (see :meth:`KeyRepository.insert`). The plaintext
    itself is NEVER persisted (NFR-02 / AC-3.2).
    """

    id: int
    key_hash: str
    scope: str
    created_at: datetime
    revoked_at: Optional[datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: ApiKeyRow) -> dict:
    return {
        "id": row.id,
        "key_hash": row.key_hash,
        "scope": row.scope,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
    }


class KeyRepository:
    """[FR-03] Api-key persistence.

    Public surface (used by ``service.auth`` + the
    ``python -m taskq_api key create`` CLI):

    * ``insert(key_hash, scope, revoked_at=None)``  — store a new row
    * ``find_active_by_hash(key_hash)``            — AC-3.4: omit
      revoked rows (returns ``None`` for missing or revoked)
    * ``find_by_hash(key_hash)``                   — read-only access
      used by tests for AC-3.2 (stores hashed values, never plaintext)
    * ``now()``                                    — clock helper so
      tests can stamp deterministic ``revoked_at`` rows

    The store is in-memory (process-local). The SQL-backed shape lives
    in :class:`taskq_api.models.orm.ApiKey`; the public method names
    here match the L2 contract so swapping the body to a SQLAlchemy
    session does not touch callers.
    """

    def now(self) -> datetime:
        """[FR-03] Return a UTC timestamp for test/CLI stamping."""
        return _now()

    def insert(
        self,
        key_hash: str,
        scope: str,
        revoked_at: Optional[datetime] = None,
    ) -> dict:
        """[FR-03 AC-3.2, AC-3.4] Persist one ``api_keys`` row.

        ``key_hash`` is the 64-char SHA-256 hex digest of the plaintext
        — the plaintext is never stored. ``revoked_at`` is non-null
        only for retired keys (AC-3.4 contract — ``find_active_by_hash``
        filters them out).
        """
        with _LOCK:
            row_id = uuid.uuid4().int & 0x7FFFFFFF  # monotonic-ish int PK
            row = ApiKeyRow(
                id=row_id,
                key_hash=key_hash,
                scope=scope,
                created_at=_now(),
                revoked_at=revoked_at,
            )
            _KEYS[key_hash] = row
            return _row_to_dict(row)

    def find_by_hash(self, key_hash: str) -> Optional[dict]:
        """[FR-03 AC-3.2] Read a single ``api_keys`` row by hash.

        Returns the row dict (canonical columns ``key_hash``,
        ``scope``, ``revoked_at``) or ``None`` for an unknown hash.
        Revoked rows are returned here — callers that need to filter
        out revoked keys must use :meth:`find_active_by_hash`.
        """
        with _LOCK:
            row = _KEYS.get(key_hash)
            return _row_to_dict(row) if row else None

    def find_active_by_hash(self, key_hash: str) -> Optional[dict]:
        """[FR-03 AC-3.4] Return the row only if ``revoked_at`` is null.

        This is the hot read path for ``service.auth.verify_key`` — a
        non-null ``revoked_at`` (state-machine retirement) yields
        ``None``, which the auth layer turns into HTTP 401.
        """
        with _LOCK:
            row = _KEYS.get(key_hash)
            if row is None or row.revoked_at is not None:
                return None
            return _row_to_dict(row)


__all__ = ["KeyRepository", "ApiKeyRow"]
