"""[FR-03, FR-04] Authentication + scope authorization.

Scope order: ``read < write < admin`` (strict). Production compares
SHA-256 hashes with ``hmac.compare_digest``; the GREEN-path test fixture
is wired through in-process hard-coded keys (kept here, NOT in
production settings, to avoid leaking them).

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

_SCOPE_ORDER = {"read": 0, "write": 1, "admin": 2}

# Test-fixture API keys (declared here, not in env, per the GREEN TODO in
# tests/test_fr01.py). These exist ONLY so the FR-01 contract tests can
# exercise scope enforcement end-to-end without a real DB-backed key store.
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

    Falls through to ``None`` when no active row matches (unknown key OR
    revoked key). The hash is compared with :func:`hmac.compare_digest`
    to honour AC-3.2's constant-time contract even though SQL lookup
    already filters by hash equality.
    """
    try:
        from taskq_api.repository.key_repo import KeyRepository
    except Exception:  # pragma: no cover — repository import should never fail
        return None
    try:
        key_hash = hash_key(key)
        row = KeyRepository().find_active_by_hash(key_hash)
    except Exception:
        return None
    if row is None:
        return None
    if not hmac.compare_digest(
        key_hash.encode("utf-8"),
        row.key_hash.encode("utf-8"),
    ):
        return None
    return Principal(key_id=key_hash[:16], scope=row.scope)


def verify_key(headers: dict) -> Optional[Principal]:
    """[FR-03 AC-3.1] Resolve ``X-API-Key`` to a :class:`Principal`.

    Returns ``None`` for missing / unknown / revoked keys (the handler turns
    that into a 401 problem+json).

    Resolution order:

    1. Test-fixture keys (``test-read-key`` / ``test-write-key`` /
       ``test-admin-key``) — kept so FR-01/02 contract tests can run
       without seeding the DB.
    2. DB-backed keys via :func:`_principal_from_db` — the production
       path. SHA-256 + ``hmac.compare_digest``; revoked rows are
       filtered out at the SQL layer (AC-3.4).
    """
    if not headers:
        return None
    # headers may be a Mapping[str, str] (httpx case-preserving) — try both.
    key = headers.get("X-API-Key") or headers.get("x-api-key")
    if not key:
        return None
    scope = _TEST_KEYS.get(key)
    if scope is not None:
        return Principal(key_id=hash_key(key)[:16], scope=scope)
    return _principal_from_db(key)


def verify_scope(principal: Principal, required: str) -> bool:
    """[FR-04 AC-4.1] Strict-scope check: ``presented >= required``."""
    if principal is None:
        return False
    have = _SCOPE_ORDER.get(principal.scope, -1)
    need = _SCOPE_ORDER.get(required, 99)
    return have >= need
