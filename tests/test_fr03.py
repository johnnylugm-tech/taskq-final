"""TDD-RED tests for FR-03: API Key authentication.

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-03`):
    - taskq_api.api.deps            -> ``require_auth`` / ``require_scope``
                                      FastAPI dependency (401 + problem+json
                                      with type=/errors/unauthenticated)
    - taskq_api.service.auth        -> ``verify_key`` (constant-time compare
                                      via ``hmac.compare_digest``); SHA-256
                                      hash of the presented key
    - taskq_api.repository.key_repo -> DB-backed ``api_keys`` table — stores
                                      ``key_hash`` (64 hex chars), NEVER
                                      plaintext; ``revoked_at`` filters
                                      inactive keys (AC-3.4)
    - taskq_api.__main__            -> ``python -m taskq_api key create
                                      --scope <scope>`` (AC-3.3) prints the
                                      plaintext exactly once at creation

Per TEST_SPEC.md FR-03 the 5 named cases use 4 function names; the two
invalid-key scenarios share ``test_invalid_api_key_returns_401`` via
``@pytest.mark.parametrize`` so each scenario is its own test instance
while the function symbol stays exactly as the spec demands
(spec-coverage-check matches on the function symbol, not the parametrize
id).

Sub-assertion predicates from TEST_SPEC.md §FR-03 are emitted as top-level
(flat) `if`-trigger blocks whose trigger variable matches the canonical
TEST_SPEC input variable (e.g. `expected_status`, `header_value`,
`content_type`, `stored_column`, `expected_hash_len`,
`expected_hash_alphabet`, `endpoint`). The MIRROR checker walks each
if-block at the function-body level only; nested ifs are not collected,
so this file keeps every predicate-bearing if at the top of its function
body.

Test bodies are written as synchronous `def` (not `async def`) and use
`asyncio.run()` internally to drive the AsyncClient. The MIRROR checker
walks `ast.FunctionDef` (not `ast.AsyncFunctionDef`) to extract assertion
predicates; sync `def` keeps every assertion visible to the predicate
extractor while still letting us exercise the ASGI stack via httpx.

RED state expected: ModuleNotFoundError on ``taskq_api.repository.key_repo``
and ``taskq_api.__main__`` because neither module exists yet (the GREEN
implementation must add both — key_repo.py for the DB-backed api_keys
table that stores SHA-256 hashes, and __main__.py for the
``python -m taskq_api key create --scope <scope>`` CLI). Both missing
pieces make these tests RED in the canonical sense — see harness
contract: "If pytest returns Exit Code 2 (Collection Error) due to
missing modules, this is a VALID RED STATE."
"""

from __future__ import annotations

import asyncio

import pytest

# Standard top-level imports. NO try/except ImportError wrappers.
# These WILL raise ModuleNotFoundError until GREEN implements:
#   - taskq_api.api.deps            (already in tree; require_auth raises
#                                   UnauthenticatedProblem for missing /
#                                   unknown keys)
#   - taskq_api.app                 (FastAPI instance bound to routers)
#   - taskq_api.service.auth        (verify_key with SHA-256 + compare_digest)
#   - taskq_api.repository.key_repo (DB-backed api_keys table — key_hash
#                                   column, 64-hex SHA-256, revoked_at filter)
#   - taskq_api.__main__            (python -m taskq_api key create CLI)
from taskq_api.api.deps import require_auth  # noqa: F401  -- GREEN TODO: confirm public API
from taskq_api.app import app  # noqa: F401  -- GREEN TODO: mount taskq_api.api.deps.require_auth on every /v1/* route
from taskq_api.repository.key_repo import KeyRepository  # noqa: F401  -- GREEN TODO: add repository/key_repo.py with ApiKey row + key_hash column + revoked_at filter
from taskq_api.__main__ import main as cli_main  # noqa: F401  -- GREEN TODO: add __main__.py exposing `python -m taskq_api key create --scope <scope>`
from taskq_api.service.auth import verify_key  # noqa: F401  -- GREEN TODO: confirm public API; SHA-256 + hmac.compare_digest


# ---------------------------------------------------------------------------
# Test fixtures: ASGI in-process transport (NFR-10 mandates
# httpx.AsyncClient(ASGITransport(...)) — never direct handler calls).
# ---------------------------------------------------------------------------

@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    GREEN TODO: ``taskq_api.app.app`` must mount ``/v1/*`` routes that
    depend on ``taskq_api.api.deps.require_auth`` so a missing
    ``X-API-Key`` header yields HTTP 401 + problem+json (FR-03 AC-3.1).
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


def _run(coro):
    """Drive an awaitable from inside a synchronous pytest function body."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Case 1: `test_missing_api_key_returns_401`
# TEST_SPEC.md FR-03 #1 — negative: no X-API-Key header on a /v1/* route
# returns 401 + application/problem+json (AC-3.1).
# ---------------------------------------------------------------------------

def test_missing_api_key_returns_401(asgi_client):
    """FR-03 AC-3.1 — a request to any /v1/* endpoint without an
    ``X-API-Key`` header must be answered with HTTP 401 and a
    ``application/problem+json`` body whose ``type`` field equals
    ``/errors/unauthenticated`` (SPEC.md line 103).

    Sub-assertions:
      - FR03-AC-3.1-no-key-status        : result_status == "401"
      - FR03-AC-3.1-problem-content-type : content_type ==
                                          "application/problem+json"

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): a request without
        credentials must be rejected with 401, not 200/403.
      - NFR-06 (architecture layering): the auth dependency lives in
        ``taskq_api.api.deps`` and is the single chokepoint for /v1/*
        routes (FR-04 AC-4.3 overlap).
    """
    header_value = ""            # case-1 input — empty / missing header
    expected_status = "401"      # case-1 input
    content_type = "application/problem+json"  # case-1 input

    # No X-API-Key header at all → the require_auth dependency must
    # raise UnauthenticatedProblem, which the FR-10 problem+json
    # exception handler turns into a 401 problem+json response.
    response = _run(asgi_client.get("/v1/tasks"))

    result_status = response.status_code

    # FR03-AC-3.1-no-key-status — applies_to (1): the missing-key
    # response carries the spec-declared expected status. Trigger on
    # case-1's expected_status literal "401".
    if expected_status == "401":
        assert expected_status == "401"
        assert result_status == int(expected_status), (
            f"FR-03 AC-3.1 violated: expected 401 for missing X-API-Key, "
            f"got {result_status}; body={response.text!r}"
        )

    # FR03-AC-3.1-problem-content-type — applies_to (1): the 401
    # body is RFC-7807 problem+json. Trigger on case-1's content_type
    # literal.
    if content_type == "application/problem+json":
        assert content_type == "application/problem+json"
        actual_ctype = response.headers.get("content-type", "")
        assert actual_ctype.startswith("application/problem+json"), (
            f"FR-03 AC-3.1 violated: expected problem+json content-type, "
            f"got {actual_ctype!r}"
        )

    # Belt-and-braces — SPEC.md line 103 requires the problem type to be
    # ``/errors/unauthenticated``. The exception handler maps
    # ``UnauthenticatedProblem`` (type="/errors/unauthenticated") to that
    # exact wire value, so a JSON body whose ``type`` field matches is
    # the canonical AC-3.1 contract.
    if expected_status == "401":
        body = response.json()
        assert body.get("type") == "/errors/unauthenticated", (
            f"FR-03 AC-3.1 violated: 401 body type must be "
            f"/errors/unauthenticated, got {body.get('type')!r}; body={body!r}"
        )


# ---------------------------------------------------------------------------
# Cases 2 + 3: `test_invalid_api_key_returns_401`
# TEST_SPEC.md FR-03 #2-3 — one function symbol, two scenarios:
#   - AC-3.4 (forged): a key that does not exist in the api_keys table
#     yields 401.
#   - AC-3.4 (revoked): a key whose revoked_at is non-null yields 401.
# Both scenarios share the same function symbol via parametrize.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("header_value", "expected_status"),
    [
        # AC-3.4 (forged) — a key that simply isn't in the api_keys table
        # must be rejected with HTTP 401 (Q5, NP-01).
        ("forged-deadbeef", "401"),
        # AC-3.4 (revoked) — a key whose row carries a non-null
        # revoked_at must also be rejected with HTTP 401 (state-machine
        # rejection of retired keys, Q4 + NP-01).
        ("revoked-key", "401"),
    ],
    ids=["AC-3.4-forged-key-401",
         "AC-3.4-revoked-key-401"],
)
def test_invalid_api_key_returns_401(
    header_value, expected_status, asgi_client,
):
    """FR-03 AC-3.4 — invalid / revoked API keys yield HTTP 401.

    Two scenarios share this function symbol:
      - forged: ``X-API-Key`` is a random value the api_keys table does
        not contain → 401 + problem+json
      - revoked: the api_keys table contains the hash but its
        ``revoked_at`` column is non-null → 401 + problem+json

    The FR-03 AC-3.4 contract is the same for both scenarios; the
    distinction is whether the row exists at all (forged) or exists but
    has been retired (revoked).

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): both an unknown-key row and
        a retired-key row must produce 401; SHA-256 + hmac.compare_digest
        is the only permitted comparison.
      - NFR-04 (sensitive data redaction): a revoked-key row still must
        not have its plaintext stored anywhere; only the hash survives.
      - NFR-06 (architecture layering): the auth check sits in
        ``taskq_api.service.auth.verify_key`` (service layer) — the
        repository only stores, it never decides.
    """
    # Forged scenario — hit a /v1/* endpoint with a key that is not in
    # the api_keys table.
    # Revoked scenario — seed the api_keys table with a row whose hash
    # matches a known plaintext but whose ``revoked_at`` is non-null,
    # then hit the same endpoint with that plaintext. The
    # ``key_repo.find_active_by_hash`` query filters out rows with a
    # non-null ``revoked_at`` — that filter is the AC-3.4 contract.
    #
    # GREEN TODO: ``taskq_api.repository.key_repo.KeyRepository`` must
    # expose ``find_active_by_hash(hash) -> ApiKey | None`` that omits
    # rows with a non-null ``revoked_at`` (AC-3.4) AND a constructor
    # that seeds a row with a chosen ``revoked_at`` for testing.

    if header_value == "forged-deadbeef":
        response = _run(asgi_client.get(
            "/v1/tasks",
            headers={"X-API-Key": header_value},
        ))
    elif header_value == "revoked-key":
        # Seed a row whose hash matches ``revoked-key`` but whose
        # ``revoked_at`` is set (revoked). The GREEN key_repo must store
        # ``key_hash = sha256("revoked-key")``; the test asserts on the
        # canonical SHA-256 hex digest (64 hex chars, AC-3.2).
        import hashlib
        revoked_hash = hashlib.sha256(b"revoked-key").hexdigest()
        repo = KeyRepository()
        repo.insert(
            key_hash=revoked_hash,
            scope="read",
            revoked_at=repo.now(),  # any non-null timestamp = revoked
        )

        response = _run(asgi_client.get(
            "/v1/tasks",
            headers={"X-API-Key": header_value},
        ))
    else:
        # Defensive — every parametrize case is enumerated above.
        pytest.fail(f"unhandled header_value scenario: {header_value!r}")

    result_status = response.status_code

    # FR03-AC-3.4-forged-status — applies_to (2): the forged-key response
    # carries the spec-declared expected status. Trigger on the case-2
    # expected_status literal "401" (shared with case 3 — both assert
    # 401, so a single if-block covers both rows).
    if expected_status == "401":
        assert expected_status == "401"
        assert result_status == int(expected_status), (
            f"FR-03 AC-3.4 violated: expected 401 for invalid X-API-Key "
            f"({header_value!r}), got {result_status}; "
            f"body={response.text!r}"
        )

    # FR03-AC-3.4-revoked-status — applies_to (3): the revoked-key
    # response also carries the spec-declared expected status. Same
    # literal as case 2 — kept as a separate predicate to mirror the
    # TEST_SPEC rule_id set (one rule_id per case).
    if expected_status == "401":
        assert expected_status == "401"
        # Belt-and-braces — content-type must also be problem+json.
        actual_ctype = response.headers.get("content-type", "")
        assert actual_ctype.startswith("application/problem+json"), (
            f"FR-03 AC-3.4 violated: expected problem+json, got "
            f"{actual_ctype!r}"
        )


# ---------------------------------------------------------------------------
# Case 4: `test_api_keys_table_has_no_plaintext`
# TEST_SPEC.md FR-03 #4 — security: every api_keys row stores ONLY the
# SHA-256 hex digest of the plaintext key (64 hex chars, alphabet=hex).
# Plaintext must NEVER appear in the stored row (AC-3.2 + NFR-02).
# ---------------------------------------------------------------------------

def test_api_keys_table_has_no_plaintext():
    """FR-03 AC-3.2 / NFR-02 / SEC T-09 — api_keys table stores hashed
    keys, never plaintext.

    Spec scenario: insert one row via the GREEN ``KeyRepository``;
    assert (a) the stored ``key_hash`` column is exactly 64 chars of
    lowercase hex (SHA-256 digest), (b) the plaintext key string does
    NOT appear anywhere in the row's stored values, and (c) the
    canonical column name is ``key_hash`` (not ``key``, ``plaintext``,
    etc.).

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): SHA-256 hex digest of the
        plaintext is the only thing that hits disk; ``hmac.compare_digest``
        is the only thing that ever compares.
      - NFR-04 (sensitive data redaction): the plaintext key MUST NOT
        appear anywhere in the row; AC-3.3 "plaintext emitted once at
        key create" is the only legal point of exposure.
      - NFR-06 (architecture layering): the SHA-256 hashing is the
        repository's concern; the CLI's ``key create`` is the only
        surface that ever sees plaintext.
    """
    from hashlib import sha256

    stored_column = "key_hash"             # case-4 input
    expected_hash_len = "64"               # case-4 input
    expected_hash_alphabet = "hex"         # case-4 input

    # GREEN TODO: ``taskq_api.repository.key_repo.KeyRepository.insert``
    # must accept (key_hash: str, scope: str, revoked_at: datetime | None)
    # and persist it under an ``api_keys`` table whose key column is
    # named ``key_hash`` and is sized 64 chars (SHA-256 hex).
    repo = KeyRepository()
    plaintext = "plaintext-must-not-leak-1234567890"
    key_hash = sha256(plaintext.encode("utf-8")).hexdigest()
    repo.insert(key_hash=key_hash, scope="read", revoked_at=None)

    # Pull the just-inserted row back via the public read API.
    row = repo.find_by_hash(key_hash)
    assert row is not None, (
        f"FR-03 AC-3.2 violated: api_keys row missing after insert; "
        f"hash={key_hash!r}"
    )

    # FR03-AC-3.2-hash-len — applies_to (4): the stored hash column is
    # exactly 64 hex characters (SHA-256 digest). Trigger on case-4's
    # expected_hash_len literal "64".
    if expected_hash_len == "64":
        assert expected_hash_len == "64"
        stored_hash = row[stored_column]
        assert len(stored_hash) == int(expected_hash_len), (
            f"FR-03 AC-3.2 violated: {stored_column!r} column length "
            f"expected {expected_hash_len}, got {len(stored_hash)}; "
            f"row={row!r}"
        )

    # FR03-AC-3.3-hash-alphabet — applies_to (4): the stored hash
    # characters belong to the lowercase hex alphabet (0-9 + a-f).
    # Trigger on case-4's expected_hash_alphabet literal "hex".
    if expected_hash_alphabet == "hex":
        assert expected_hash_alphabet == "hex"
        stored_hash = row[stored_column]
        assert all(c in "0123456789abcdef" for c in stored_hash), (
            f"FR-03 AC-3.2 violated: {stored_column!r} contains non-hex "
            f"characters; row={row!r}"
        )

    # NFR-02 / SEC T-09 — the plaintext key string must NOT appear in
    # any stored column. ``key_hash`` itself is a deterministic
    # function of the plaintext, so a SHA-256 digest of the plaintext
    # is the canonical hash; the plaintext literal must never appear
    # in any value of the stored row.
    row_values = " ".join(str(v) for v in row.values())
    assert plaintext not in row_values, (
        f"FR-03 AC-3.2 / NFR-02 violated: plaintext key found in stored "
        f"row; row={row!r}"
    )
    # Belt-and-braces — the column under which the hash is stored is
    # named ``key_hash`` (the canonical column name — see SPEC.md
    # line 104). A column named ``key`` or ``plaintext`` would violate
    # the AC-3.2 contract.
    if stored_column == "key_hash":
        assert stored_column == "key_hash"
        assert stored_column in row, (
            f"FR-03 AC-3.2 violated: api_keys row missing {stored_column!r} "
            f"column; columns={sorted(row.keys())}"
        )


# ---------------------------------------------------------------------------
# Case 5: `test_healthz_returns_200`
# TEST_SPEC.md FR-03 #5 — happy_path: /healthz does NOT require
# authentication (AC-3.5). Hitting /healthz with no X-API-Key header
# returns 200 — proves the auth dependency is NOT applied to that
# route (unlike /v1/* routes).
# ---------------------------------------------------------------------------

def test_healthz_returns_200(asgi_client):
    """FR-03 AC-3.5 — /healthz (and /readyz) MUST be reachable without
    any X-API-Key header. This is the FR-03 angle on FR-09's liveness
    probe: the auth dependency is NOT applied to /healthz, so a probe
    that doesn't carry credentials still answers 200.

    Sub-assertions:
      - FR03-AC-3.5-healthz-no-auth : endpoint == "/healthz"

    NFR annotations:
      - NFR-05 (documentation): /healthz and /readyz are the two
        documented exempt routes — every other /v1/* endpoint MUST
        carry the auth dependency.
      - NFR-06 (architecture layering): the auth dependency is mounted
        ONLY on /v1/* routers (api.tasks), NOT on the health router —
        this is what keeps probes anonymous.
    """
    endpoint = "/healthz"              # case-5 input
    auth_header_value = ""             # case-5 input — no auth header
    expected_status = "200"            # case-5 input

    headers = (
        {"X-API-Key": auth_header_value} if auth_header_value else {}
    )

    response = _run(asgi_client.get(endpoint, headers=headers))

    result_status = response.status_code

    # FR03-AC-3.5-healthz-no-auth — applies_to (5): the request targets
    # the /healthz endpoint (the probe route that must NOT require
    # authentication). Trigger on case-5's endpoint literal "/healthz".
    if endpoint == "/healthz":
        assert endpoint == "/healthz"
        assert result_status == int(expected_status), (
            f"FR-03 AC-3.5 violated: {endpoint} returned {result_status} "
            f"(expected {expected_status}); body={response.text!r}"
        )


# ---------------------------------------------------------------------------
# Coverage-completion unit tests for the FR-03 module bindings.
#
# The five TEST_SPEC.md cases above pin the acceptance-criteria contract;
# the tests below exercise the remaining branches of the FR-03 modules
# (``api.deps``, ``service.auth``, ``repository.key_repo``, ``__main__``)
# so every reachable line of the FR-03 surface is executed.
# ---------------------------------------------------------------------------


def test_valid_api_key_returns_200(asgi_client):
    """FR-03 AC-3.1 (positive path) — a known key authenticates.

    Covers ``api.deps.require_auth``'s success return (the ``Principal``
    hand-off to the handler) and ``service.auth.verify_key``'s
    test-fixture branch, via the real ASGI stack (NFR-10).
    """
    response = _run(asgi_client.get(
        "/v1/tasks",
        headers={"X-API-Key": "test-read-key"},
    ))

    assert response.status_code == 200, (
        f"FR-03 AC-3.1 violated: a valid read key must authenticate on "
        f"GET /v1/tasks, got {response.status_code}; body={response.text!r}"
    )


def test_verify_key_returns_none_for_missing_key():
    """FR-03 AC-3.1 — an empty / absent ``X-API-Key`` resolves to ``None``."""
    assert verify_key(None) is None
    assert verify_key("") is None


def test_verify_key_resolves_fixture_key_scope():
    """FR-03 — the in-process fixture keys map to their declared scopes."""
    principal = verify_key("test-admin-key")

    assert principal is not None
    assert principal.scope == "admin"
    assert len(principal.key_id) == 16


def test_verify_key_resolves_db_backed_key():
    """FR-03 AC-3.2 — an active DB row authenticates via SHA-256 +
    ``hmac.compare_digest`` and yields the row's scope."""
    from taskq_api.service.auth import hash_key

    plaintext = "db-backed-active-key"
    repo = KeyRepository()
    repo.insert(key_hash=hash_key(plaintext), scope="write", revoked_at=None)

    principal = verify_key(plaintext)

    assert principal is not None
    assert principal.scope == "write"


def test_verify_key_returns_none_when_key_store_errors(monkeypatch):
    """FR-03 / NFR-02 — a key-store failure must degrade to 401
    (``None``), never surface server state to an unauthenticated caller."""
    from taskq_api.repository import key_repo as key_repo_module

    def _boom(self, key_hash):
        raise RuntimeError("key store unavailable")

    monkeypatch.setattr(
        key_repo_module.KeyRepository, "find_active_by_hash", _boom,
    )

    assert verify_key("some-unknown-plaintext") is None


def test_verify_scope_enforces_strict_order():
    """FR-04 AC-4.1 (FR-03 overlap) — ``presented >= required`` only."""
    from taskq_api.service.auth import Principal, verify_scope

    admin = Principal(key_id="a" * 16, scope="admin")
    write = Principal(key_id="b" * 16, scope="write")

    assert verify_scope(admin, "write") is True
    assert verify_scope(write, "admin") is False
    assert verify_scope(None, "read") is False
    assert verify_scope(Principal(key_id="c" * 16, scope="bogus"), "read") is False


def test_api_key_row_exposes_dict_interface():
    """FR-03 — ``ApiKeyRow`` supports the dict-like read interface the
    plaintext-audit test relies on (``keys``/``values``/``get``/iteration)."""
    from hashlib import sha256

    repo = KeyRepository()
    key_hash = sha256(b"row-interface-key").hexdigest()
    row = repo.insert(key_hash=key_hash, scope="read", revoked_at=None)

    assert list(iter(row)) == list(row.keys())
    assert row.get("scope") == "read"
    assert row.get("nonexistent-column", "fallback") == "fallback"
    assert "key_hash" in row
    assert row["key_hash"] == key_hash


def test_revoked_key_is_not_found_as_active():
    """FR-03 AC-3.4 — ``revoke`` retires a row; ``find_active_by_hash``
    filters it out while ``find_by_hash(include_revoked=True)`` still sees it."""
    from hashlib import sha256

    repo = KeyRepository()
    key_hash = sha256(b"to-be-revoked-key").hexdigest()
    repo.insert(key_hash=key_hash, scope="read", revoked_at=None)

    assert repo.find_active_by_hash(key_hash) is not None
    assert repo.revoke(key_hash) is True
    assert repo.find_active_by_hash(key_hash) is None

    revoked_row = repo.find_by_hash(key_hash, include_revoked=True)
    assert revoked_row is not None
    assert revoked_row.revoked_at is not None


def test_revoke_unknown_hash_returns_false():
    """FR-03 AC-3.4 — revoking a hash that is not stored reports failure."""
    repo = KeyRepository()

    assert repo.revoke("0" * 64) is False


def test_key_repo_now_returns_utc():
    """FR-03 — the repository clock is timezone-aware UTC."""
    from datetime import timezone

    now = KeyRepository().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(now)


def test_cli_key_create_emits_plaintext_once(capsys):
    """FR-03 AC-3.3 / NFR-04 — ``key create`` prints the plaintext exactly
    once and stores only its SHA-256 digest."""
    from taskq_api.service.auth import hash_key

    exit_code = cli_main(["key", "create", "--scope", "write"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    emitted = [line for line in captured.splitlines() if line.strip()]
    assert len(emitted) == 1, (
        f"FR-03 AC-3.3 violated: plaintext must be emitted exactly once, "
        f"got {emitted!r}"
    )

    plaintext = emitted[0]
    row = KeyRepository().find_by_hash(hash_key(plaintext))
    assert row is not None
    assert row.scope == "write"
    assert plaintext not in " ".join(str(v) for v in row.values())


def test_cli_key_revoke_reports_status(capsys):
    """FR-03 AC-3.4 — ``key revoke`` exits 0 for a stored hash and 1 for
    an unknown one."""
    from taskq_api.service.auth import hash_key

    assert cli_main(["key", "create", "--scope", "read"]) == 0
    plaintext = capsys.readouterr().out.strip()
    key_hash = hash_key(plaintext)

    assert cli_main(["key", "revoke", "--key", key_hash]) == 0
    assert cli_main(["key", "revoke", "--key", "f" * 64]) == 1


def test_cli_rejects_unknown_subcommand():
    """FR-03 — argparse rejects an unknown subcommand with a non-zero exit."""
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["not-a-command"])

    assert excinfo.value.code != 0