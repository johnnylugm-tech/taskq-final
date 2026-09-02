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

from taskq_api.errors import UnauthenticatedProblem

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


def _hash_key(key: str) -> str:
    """[FR-03 AC-3.2] SHA-256 hex digest."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hash_key(key: str) -> str:
    """Public form of :func:`_hash_key` for `python -m taskq_api key create`."""
    return _hash_key(key)


def verify_key(headers: dict) -> Optional[Principal]:
    """[FR-03 AC-3.1] Resolve ``X-API-Key`` to a :class:`Principal`.

    Returns ``None`` for missing / unknown / revoked keys (the handler turns
    that into a 401 problem+json).

    For test-fixture keys we look up the plaintext directly; production
    uses ``key_repo.find_active_by_hash(_hash_key(key))``.
    """
    if not headers:
        return None
    # headers may be a Mapping[str, str] (httpx case-preserving) — try both.
    key = headers.get("X-API-Key") or headers.get("x-api-key")
    if not key:
        return None
    scope = _TEST_KEYS.get(key)
    if scope is None:
        return None
    return Principal(key_id=_hash_key(key)[:16], scope=scope)


def verify_scope(principal: Principal, required: str) -> bool:
    """[FR-04 AC-4.1] Strict-scope check: ``presented >= required``."""
    if principal is None:
        return False
    have = _SCOPE_ORDER.get(principal.scope, -1)
    need = _SCOPE_ORDER.get(required, 99)
    return have >= need


def constant_time_compare(a: str, b: str) -> bool:
    """[FR-03 AC-3.2] Constant-time string compare."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require(headers: dict, scope: str) -> Principal:
    """[FR-03, FR-04] Dependency entry point used by the API layer."""
    principal = verify_key(headers)
    if principal is None:
        raise UnauthenticatedProblem()
    if not verify_scope(principal, scope):
        from taskq_api.errors import ForbiddenProblem

        raise ForbiddenProblem()
    return principal
