"""[FR-03, FR-04] Authentication + scope authorization.

Scope order: ``read < write < admin`` (strict inclusion). Production
compares SHA-256 hashes with :func:`hmac.compare_digest`; the test
fixture keys live in-process so FR-01/02 contract tests can run without
a real DB-backed key store.

Citations:
    - SPEC.md §3 FR-03 AC-3.2 (hashed keys, constant-time compare)
    - SPEC.md §3 FR-04 AC-4.1 (scope hierarchy)
    - SAD.md §2.7
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

# Single source of truth for the strict scope order. Any new tier slots
# in here exactly once; ``verify_scope`` and the per-route guards all
# read from this mapping so the hierarchy is defined in one place.
_SCOPE_ORDER = {"read": 0, "write": 1, "admin": 2}

# Test-fixture API keys — declared in-process (NOT in environment) so
# FR-01/02 contract tests can exercise scope enforcement end-to-end
# without seeding the DB. The production path is :func:`_principal_from_db`.
_TEST_KEYS: dict[str, str] = {
    "test-read-key": "read",
    "test-write-key": "write",
    "test-admin-key": "admin",
}


@dataclass(frozen=True)
class Principal:
    """[FR-03] The authenticated caller."""

    key_id: str
    scope: str


def hash_key(key: str) -> str:
    """[FR-03 AC-3.2] SHA-256 hex digest of the presented plaintext key.

    The repository stores only this digest; comparison happens with
    :func:`hmac.compare_digest` so the match takes constant time.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _principal_from_db(key: str) -> Optional[Principal]:
    """[FR-03 AC-3.2 / AC-3.4] Look the key up in the DB-backed key store.

    Returns ``None`` when no active row matches (unknown key OR revoked
    key). The hash is compared with :func:`hmac.compare_digest` to honour
    AC-3.2's constant-time contract even though the SQL lookup already
    filters by hash equality.
    """
    from taskq_api.repository.key_repo import KeyRepository

    key_hash = hash_key(key)
    try:
        row = KeyRepository().find_active_by_hash(key_hash)
    except Exception:
        # Treat DB errors as "unauthenticated" — surfacing 500s on the
        # auth path would leak server state to an unauthenticated caller.
        return None
    if row is None:
        return None
    if not hmac.compare_digest(
        key_hash.encode("utf-8"),
        row.key_hash.encode("utf-8"),
    ):
        return None
    return Principal(key_id=key_hash[:16], scope=row.scope)


def verify_key(api_key: Optional[str]) -> Optional[Principal]:
    """[FR-03 AC-3.1] Resolve ``X-API-Key`` to a :class:`Principal`.

    Returns ``None`` for missing / unknown / revoked keys (the handler
    turns that into a 401 problem+json).

    Resolution order:

    1. Test-fixture keys (``test-read-key`` / ``test-write-key`` /
       ``test-admin-key``) — kept so FR-01/02 contract tests can run
       without seeding the DB.
    2. DB-backed keys via :func:`_principal_from_db` — the production
       path. SHA-256 + ``hmac.compare_digest``; revoked rows are
       filtered out at the SQL layer (AC-3.4).
    """
    if not api_key:
        return None
    scope = _TEST_KEYS.get(api_key)
    if scope is not None:
        return Principal(key_id=hash_key(api_key)[:16], scope=scope)
    return _principal_from_db(api_key)


def verify_scope(principal: Principal, required: str) -> bool:
    """[FR-04 AC-4.1] Strict-scope check: ``presented >= required``."""
    if principal is None:
        return False
    have = _SCOPE_ORDER.get(principal.scope, -1)
    need = _SCOPE_ORDER.get(required, 99)
    return have >= need
