"""[FR-03] API-key repository — DB-backed ``api_keys`` table.

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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from taskq_api.models.orm import ApiKey
from taskq_api.repository.session import transaction


def _now() -> datetime:
    """[FR-03] UTC timestamp used for both ``created_at`` and test
    ``revoked_at`` markers."""
    return datetime.now(timezone.utc)


@dataclass
class ApiKeyRow:
    """[FR-03] In-memory projection of an ``api_keys`` row.

    Used so the FR-03 service layer / tests can talk in dict-style keys
    (the test asserts ``row[stored_column]`` against ``key_hash``).
    """

    id: int
    key_hash: str
    scope: str
    created_at: datetime
    revoked_at: Optional[datetime]

    def __getitem__(self, item: str):
        return getattr(self, item)

    def __contains__(self, item: object) -> bool:
        return item in self.keys()

    def __iter__(self):
        return iter(self.keys())

    def values(self):
        return {
            "id": self.id,
            "key_hash": self.key_hash,
            "scope": self.scope,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }.values()

    def keys(self):
        return ("id", "key_hash", "scope", "created_at", "revoked_at")

    def get(self, key: str, default=None):
        return getattr(self, key, default)


def _to_row(orm_row: ApiKey) -> ApiKeyRow:
    return ApiKeyRow(
        id=int(orm_row.id),
        key_hash=orm_row.key_hash,
        scope=orm_row.scope,
        created_at=orm_row.created_at,
        revoked_at=orm_row.revoked_at,
    )


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
        with transaction() as session:
            row = ApiKey(
                key_hash=key_hash,
                scope=scope,
                created_at=_now(),
                revoked_at=revoked_at,
            )
            session.add(row)
            session.flush()
            return _to_row(row)

    def find_by_hash(self, key_hash: str) -> Optional[ApiKeyRow]:
        """[FR-03] Look up one row by hash, regardless of revocation state."""
        with transaction() as session:
            stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
            orm_row = session.execute(stmt).scalar_one_or_none()
            if orm_row is None:
                return None
            return _to_row(orm_row)

    def find_active_by_hash(self, key_hash: str) -> Optional[ApiKeyRow]:
        """[FR-03 AC-3.4] Look up one row, omitting revoked ones."""
        with transaction() as session:
            stmt = (
                select(ApiKey)
                .where(ApiKey.key_hash == key_hash)
                .where(ApiKey.revoked_at.is_(None))
            )
            orm_row = session.execute(stmt).scalar_one_or_none()
            if orm_row is None:
                return None
            return _to_row(orm_row)

    def revoke(self, key_hash: str) -> bool:
        """[FR-03 AC-3.4] Mark one row as revoked; ``False`` if missing."""
        with transaction() as session:
            stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
            orm_row = session.execute(stmt).scalar_one_or_none()
            if orm_row is None:
                return False
            orm_row.revoked_at = _now()
            return True

    def now(self) -> datetime:
        """[FR-03] UTC clock — exposed so tests can stamp ``revoked_at``
        deterministically without importing ``datetime`` directly."""
        return _now()


__all__ = ["ApiKeyRow", "KeyRepository"]
