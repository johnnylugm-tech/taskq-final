"""[FR-03, FR-06] API-key repository — DB-backed ``api_keys`` table.
# Error handling delegated to taskq_api.repository.session.transaction() CM (NFR-03: commit/rollback on any exception).
# pragma: no error-handling

Stores ONLY the SHA-256 hex digest of the plaintext key (64 hex chars,
AC-3.2). Plaintext is never written here; the only surface that ever
sees plaintext is ``python -m taskq_api key create`` (CLI), and only at
the moment of creation.

[FR-06 AC-6.2 / NFR-03] Every write runs inside one ``transaction()``
context manager that commits on clean exit and rolls back on any
exception — the SQL store replaced the prior in-memory dict because the
FR-06 contract binds every repository call to a single transaction
boundary (SAD.md §2.6).

Citations:
    - SPEC.md §3 FR-03 AC-3.2 (SHA-256 hex storage)
    - SPEC.md §3 FR-03 AC-3.4 (revoked keys are invalid)
    - SPEC.md §3 FR-06 AC-6.2 (transaction boundary)
    - SPEC.md §4 NFR-02 (no plaintext on disk)
    - SPEC.md §4 NFR-03 (commit/rollback CM)
    - SPEC.md §4 NFR-04 (plaintext emitted once at create)
    - SAD.md §2.6 (transaction boundary), §2.7
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from taskq_api.models.orm import ApiKey

# Imported via the module object (NOT ``from ... import transaction``) so
# tests that monkeypatch ``taskq_api.repository.session.transaction`` see
# the spy when this module looks the symbol up — the local-binding form
# would shadow the patch in the function scope. FR-06 AC-6.2 /
# test_repository_methods_use_transaction_cm rely on the dynamic
# lookup path.
from taskq_api.repository import session as _session

# ``select`` is re-exported by ``session`` so this module never imports
# ``sqlalchemy`` directly — keeps the project-wide SQLAlchemy-containment
# import-linter contract green (only ``session`` and ``models.orm`` may
# touch ``sqlalchemy``).
from taskq_api.repository.session import select


def _utc_now() -> datetime:
    """[FR-03] UTC timestamp used for ``created_at`` and ``revoked_at``."""
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive ``created_at`` / ``revoked_at`` read from SQLite.

    SQLite has no timezone-aware storage, so ``DateTime(timezone=True)``
    columns round-trip as naive values. Every timestamp this repository
    writes is UTC, so re-attaching UTC restores the original instant.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


# Columns on ``ApiKeyRow`` — used by tests that read row attributes via
# the dict-like interface (``row[stored_column]``, ``row.values()``,
# ``row.keys()``, ``stored_column in row``).
_ROW_KEYS: tuple[str, ...] = ("id", "key_hash", "scope", "created_at", "revoked_at")


@dataclass
class ApiKeyRow:
    """[FR-03] Detached snapshot projection of an ``api_keys`` row.

    Decouples callers from the live ORM session — once the
    ``transaction()`` CM commits, the session closes and the underlying
    ORM instance detaches (NFR-03 — every request commits or rolls back).
    Tests that read row attributes via the dict-like interface
    (``row[col]``, ``row.values()``, ``row.keys()``, ``col in row``) see
    the same shape whether the row was just inserted or freshly fetched.
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


class KeyRepository:
    """[FR-03, FR-06] Persistence for API keys — SHA-256 hash only.

    Every mutating call enters the ``_session.transaction()`` CM
    (FR-06 AC-6.2); rows are projected into :class:`ApiKeyRow` snapshots
    inside the CM so callers receive a detached dataclass once the
    session closes (NFR-03).

    Public surface:
        - ``insert(key_hash, scope, revoked_at=None)``
        - ``find_by_hash(key_hash)``
        - ``find_active_by_hash(key_hash)``  — filters out revoked rows
        - ``revoke(key_hash)``
        - ``now()``                          — UTC clock used by tests
    """

    @staticmethod
    def _to_row(orm_key: ApiKey) -> ApiKeyRow:
        """Project a live ``ApiKey`` ORM instance into a detached snapshot.

        Must be called INSIDE the ``transaction()`` block — once the
        session closes, ORM attribute reads raise ``DetachedInstanceError``
        (NFR-03). The snapshot is what survives the close.
        """
        return ApiKeyRow(
            id=int(orm_key.id),
            key_hash=orm_key.key_hash,
            scope=orm_key.scope,
            created_at=_as_utc(orm_key.created_at),
            revoked_at=_as_utc(orm_key.revoked_at) if orm_key.revoked_at else None,
        )

    def insert(
        self,
        key_hash: str,
        scope: str,
        revoked_at: Optional[datetime] = None,
    ) -> ApiKeyRow:
        """[FR-03 AC-3.2, FR-06 AC-6.2] Persist one row with a SHA-256 hash.

        Runs inside ``transaction()`` so the row commits on clean exit and
        rolls back on any exception (NFR-03).
        """
        with _session.transaction() as db_session:
            orm_key = ApiKey(
                key_hash=key_hash,
                scope=scope,
                created_at=_utc_now(),
                revoked_at=revoked_at,
            )
            db_session.add(orm_key)
            db_session.flush()
            return self._to_row(orm_key)

    def find_by_hash(
        self, key_hash: str, *, include_revoked: bool = False,
    ) -> Optional[ApiKeyRow]:
        """[FR-03, FR-06 AC-6.2] Look up one row by hash.

        Args:
            key_hash: SHA-256 hex digest of the presented plaintext.
            include_revoked: when ``True``, return rows whose
                ``revoked_at`` is non-null too (default ``False`` honours
                AC-3.4 — revoked keys must not authenticate).

        Returns:
            The matching :class:`ApiKeyRow`, or ``None`` if no row matches.
        """
        with _session.transaction() as db_session:
            orm_key = db_session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash)
            ).scalar_one_or_none()
            if orm_key is None:
                return None
            if not include_revoked and orm_key.revoked_at is not None:
                return None
            return self._to_row(orm_key)

    def find_active_by_hash(self, key_hash: str) -> Optional[ApiKeyRow]:
        """[FR-03 AC-3.4] Look up one row, omitting revoked ones."""
        return self.find_by_hash(key_hash)

    def revoke(self, key_hash: str) -> bool:
        """[FR-03 AC-3.4, FR-06 AC-6.2] Mark one row as revoked; ``False`` if missing.

        Runs inside ``transaction()`` so the update commits on clean exit
        and rolls back on any exception (NFR-03).
        """
        with _session.transaction() as db_session:
            orm_key = db_session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash)
            ).scalar_one_or_none()
            if orm_key is None:
                return False
            orm_key.revoked_at = _utc_now()
            return True

    def now(self) -> datetime:
        """[FR-03] UTC clock — exposed so tests can stamp ``revoked_at``
        deterministically without importing ``datetime`` directly."""
        return _utc_now()


__all__ = ["ApiKeyRow", "KeyRepository"]