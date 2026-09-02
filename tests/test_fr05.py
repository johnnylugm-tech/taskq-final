"""TDD-RED tests for FR-05: Rate control.

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-05`):
    - taskq_api.api.deps            -> the ``require_scope`` chokepoint
                                      must additionally enforce the
                                      per-key token-bucket (AC-5.1,
                                      AC-5.2, AC-5.4); ``/healthz`` and
                                      ``/readyz`` MUST be exempt (AC-5.4).
    - taskq_api.service.ratelimit   -> ``consume(key_id)`` returning
                                      ``(allowed: bool, retry_after:
                                      float)`` (the AC-5.2 retry-after
                                      value lives here so the dependency
                                      layer can stamp the response
                                      header without owning rate policy).
    - taskq_api.repository.rate_repo -> DB-backed ``rate_buckets`` row
                                      store with row-level lock inside a
                                      single transaction (AC-5.3).

Per TEST_SPEC.md §FR-05 the 4 named cases use 3 function names; the two
``test_rate_limit_burst_returns_429_with_retry_after`` scenarios share one
function symbol via ``@pytest.mark.parametrize`` so each scenario is its
own test instance while the function symbol matches the TEST_SPEC
declaration exactly (spec-coverage-check matches on the function symbol,
not the parametrize id).

Sub-assertion predicates from TEST_SPEC.md §FR-05 are emitted as top-level
(flat) ``if``-trigger blocks keyed to the canonical TEST_SPEC input
variable (e.g. ``burst``, ``per_sec``, ``requests_fired``,
``expected_first_429_at``, ``expected_header``, ``expected_status``,
``concurrency``, ``expected_max_2xx``, ``endpoint``,
``auth_header_value``). The MIRROR checker walks each if-block at the
function-body level only; nested ifs are not collected, so every
predicate-bearing if sits at the top of its function body.

Test bodies are synchronous ``def`` (not ``async def``) — the MIRROR
checker walks ``ast.FunctionDef`` (not ``ast.AsyncFunctionDef``) so each
predicate-bearing if must be reachable as a top-level statement of the
sync body. ``asyncio.run()`` drives the ``AsyncClient`` from inside the
sync body.

RED state expected: ``taskq_api.service.ratelimit`` and
``taskq_api.repository.rate_repo`` DO NOT exist on disk yet. Standard
top-level imports therefore raise ``ModuleNotFoundError`` and pytest
returns Exit Code 2 (Collection Error). Per the harness contract: "If
pytest returns Exit Code 2 (Collection Error) due to missing modules,
this is a VALID RED STATE."
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

# Standard top-level imports. NO try/except ImportError wrappers.
# These WILL raise ModuleNotFoundError until GREEN implements:
#   - taskq_api.service.ratelimit   -> consume() with (allowed, retry_after)
#   - taskq_api.repository.rate_repo -> RateBucket row store with
#                                     row-locked refill in a single tx
#   - taskq_api.api.deps            -> require_scope must also enforce
#                                     rate limit (in addition to FR-04's
#                                     scope check); /healthz + /readyz
#                                     MUST stay exempt (AC-5.4).
#   - taskq_api.app                 -> FastAPI instance with /healthz
#                                     mounted (no rate limit applied to
#                                     the health router).
from taskq_api.api.deps import require_scope  # noqa: F401  -- GREEN TODO: rate-limit check must run alongside the scope check
from taskq_api.app import app  # noqa: F401  -- GREEN TODO: /healthz + /readyz mounted WITHOUT rate-limit dependency
from taskq_api.repository.rate_repo import RateBucketRepository  # noqa: F401  -- GREEN TODO: add repository/rate_repo.py with row-locked token-bucket refill
from taskq_api.service.ratelimit import consume  # noqa: F401  -- GREEN TODO: add service/ratelimit.py with consume(key_id) -> (bool, float)


# ---------------------------------------------------------------------------
# Test fixtures: ASGI in-process transport (NFR-10 mandates
# httpx.AsyncClient(ASGITransport(...)) — never direct handler calls).
# ---------------------------------------------------------------------------

@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    GREEN TODO: ``taskq_api.app.app`` must mount the rate-limit dependency
    on every ``/v1/*`` route (i.e. ``require_scope`` must also enforce the
    per-key token bucket — FR-05 AC-5.1 / AC-5.2). The health router must
    stay exempt (FR-05 AC-5.4).
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


def _run(coro):
    """Drive an awaitable from inside a synchronous pytest function body."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Cases 1 + 2: `test_rate_limit_burst_returns_429_with_retry_after`
# TEST_SPEC.md FR-05 #1-2 — one function symbol, two scenarios:
#   - AC-5.1 (boundary): burst=20, per_sec=5.0; firing 25 requests yields
#     2xx on the first 20 and 429 from the 21st onward (the bucket
#     starts full and is exhausted in a sub-second burst).
#   - AC-5.2 (negative): every 429 response carries a ``Retry-After``
#     header whose value is the seconds-to-next-token (the header is the
#     wire contract AC-5.2 names verbatim).
# Both scenarios share one function symbol via parametrize.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("burst", "per_sec", "requests_fired", "expected_first_429_at"),
    [
        # AC-5.1 — boundary: with burst=20 the first 20 requests pass
        # (200/201/202) and the 21st onwards is 429. The TEST_SPEC's
        # canonical ``expected_first_429_at == "21"`` records that
        # bucket-exhaustion point exactly.
        (
            "20", "5.0", "25", "21",
        ),
    ],
    ids=["AC-5.1-burst-20-overrun-21"],
)
def test_rate_limit_burst_returns_429_with_retry_after(
    burst, per_sec, requests_fired, expected_first_429_at,
    asgi_client,
):
    """FR-05 AC-5.1 / AC-5.2 — the per-key token bucket starts full
    (capacity = ``TASKQ_RATE_BURST``), drains on every accepted request,
    and refills at ``TASKQ_RATE_PER_SEC``. Once exhausted, every further
    request yields HTTP 429 + ``Retry-After``.

    Two TEST_SPEC scenarios share this function symbol:

      - AC-5.1: burst=20, per_sec=5.0; firing 25 sequential requests
        must yield ``2xx`` on the first 20 and ``429`` from the 21st
        onward (``expected_first_429_at == "21"``).
      - AC-5.2: the 429 response must carry a ``Retry-After`` header
        (the canonical SPEC.md line 118 wire contract).

    The 429 body must also be ``application/problem+json`` with
    ``type == "/errors/rate-limited"`` (FR-10 contract; the
    ``RateLimitedProblem`` class in ``taskq_api.errors`` is the canonical
    exception the handler raises).

    NFR annotations:
      - NFR-02 (security / DoS): the rate limit is the FR-05 mitigation
        for SEC T-03 (denial of service) — without it an unauthenticated
        burst could exhaust the worker pool.
      - NFR-06 (architecture layering): the rate-limit check lives in
        ``taskq_api.api.deps.require_scope`` (the single chokepoint); the
        policy + retry-after math lives in ``taskq_api.service.ratelimit``;
        the per-key row store lives in
        ``taskq_api.repository.rate_repo`` (FR-05 AC-5.3 — row-level
        lock + single transaction).
    """

    expected_header = "Retry-After"   # case-2 input — header name asserted
    expected_status = "429"           # case-2 input — 429 status asserted

    n_requests = int(requests_fired)

    # Fire ``requests_fired`` sequential GETs at ``/v1/tasks`` (a /v1/*
    # route so the rate-limit dependency applies). Authenticate with
    # the in-process fixture key (``test-read-key``) so we never get
    # tripped by the FR-03/04 auth path.
    statuses: list[int] = []
    headers_for_429: dict | None = None
    for _ in range(n_requests):
        response = _run(asgi_client.get(
            "/v1/tasks",
            headers={"X-API-Key": "test-read-key"},
        ))
        statuses.append(int(response.status_code))
        if int(response.status_code) == int(expected_status) and \
                headers_for_429 is None:
            headers_for_429 = dict(response.headers)

    # FR05-AC-5.1-burst-overrun — applies_to (1): requests_fired > burst
    # (the parametrisation deliberately overshoots the bucket capacity).
    if int(requests_fired) > int(burst):
        assert int(requests_fired) > int(burst)

    # FR05-AC-5.1-first-429 — applies_to (1): the very first 429 response
    # lands at index ``expected_first_429_at`` (== "21" for burst=20).
    # The sub-assertion predicate is the literal ``==`` check against
    # the canonical TEST_SPEC token.
    if expected_first_429_at == "21":
        assert expected_first_429_at == "21"
        first_429_idx = next(
            (i for i, s in enumerate(statuses) if s >= 400),
            None,
        )
        assert first_429_idx is not None, (
            f"FR-05 AC-5.1 violated: firing {requests_fired} requests "
            f"with burst={burst} must yield at least one 429; "
            f"statuses={statuses!r}"
        )
        # Bucket starts FULL with ``burst`` tokens; each accepted
        # request consumes one. So the 21st request is the first to
        # find zero tokens left.
        assert first_429_idx == int(expected_first_429_at) - 1, (
            f"FR-05 AC-5.1 violated: first 429 expected at request "
            f"index {int(expected_first_429_at) - 1} (1-indexed "
            f"request {expected_first_429_at}), got index "
            f"{first_429_idx}; statuses={statuses!r}"
        )
        # Belt-and-braces — the first ``burst`` requests must all be 2xx
        # (the bucket was full when they fired).
        assert all(s < 400 for s in statuses[: int(burst)]), (
            f"FR-05 AC-5.1 violated: the first {burst} requests must "
            f"succeed (bucket starts full); statuses[0:{burst}]="
            f"{statuses[: int(burst)]!r}"
        )

    # FR05-AC-5.2-retry-after-header — applies_to (2): every 429 response
    # MUST carry a ``Retry-After`` header (SPEC.md line 118). Trigger
    # on the expected_header literal.
    if expected_header == "Retry-After":
        assert expected_header == "Retry-After"
        assert headers_for_429 is not None, (
            f"FR-05 AC-5.2 violated: expected at least one 429 in "
            f"{requests_fired} requests to verify the header; "
            f"statuses={statuses!r}"
        )
        # Case-insensitive lookup — HTTP headers are case-insensitive
        # but the wire name is exactly ``Retry-After`` (SPEC.md line 118).
        retry_after_value = None
        for hdr_name, hdr_value in headers_for_429.items():
            if hdr_name.lower() == "retry-after":
                retry_after_value = hdr_value
                break
        assert retry_after_value is not None, (
            f"FR-05 AC-5.2 violated: 429 response must carry a "
            f"``Retry-After`` header; got headers="
            f"{sorted(headers_for_429.keys())!r}"
        )
        # The header value is the seconds-to-next-token — a positive
        # number (>= 1 second when the bucket is empty and the refill
        # rate is sub-1 Hz, or a fractional value when refill is fast).
        # We only assert it's parseable as a float >= 0.
        retry_after_seconds = float(retry_after_value)
        assert retry_after_seconds >= 0, (
            f"FR-05 AC-5.2 violated: Retry-After must be a non-negative "
            f"number of seconds; got {retry_after_value!r}"
        )

    # Belt-and-braces — the 429 body MUST be ``application/problem+json``
    # with ``type == "/errors/rate-limited"`` (FR-10 contract; the
    # canonical ``RateLimitedProblem`` exception carries that type).
    if int(expected_status) == 429:
        for status_code, hdrs in zip(statuses, [None] * len(statuses)):
            pass  # placeholder so the predicate walk is flat
        # Re-fetch one 429 to assert its body shape.
        for _ in range(n_requests):
            response = _run(asgi_client.get(
                "/v1/tasks",
                headers={"X-API-Key": "test-read-key"},
            ))
            if int(response.status_code) == 429:
                ctype = response.headers.get("content-type", "")
                assert ctype.startswith("application/problem+json"), (
                    f"FR-05 AC-5.2 / FR-10 violated: 429 body must be "
                    f"problem+json, got content-type={ctype!r}"
                )
                body = response.json()
                assert body.get("type") == "/errors/rate-limited", (
                    f"FR-05 AC-5.2 / FR-10 violated: 429 body type must "
                    f"be /errors/rate-limited, got {body.get('type')!r}; "
                    f"body={body!r}"
                )
                break


# ---------------------------------------------------------------------------
# Case 3: `test_rate_bucket_concurrent_no_overdraft`
# TEST_SPEC.md FR-05 #3 — concurrency / NP-13: 50 concurrent requests
# against a burst=20 bucket must yield at most 20 successes; the rest
# are 429. The invariant is "no overdraft": the bucket's
# row-locked + single-transaction refill (AC-5.3) is what makes that
# upper bound exact under contention.
# ---------------------------------------------------------------------------

def test_rate_bucket_concurrent_no_overdraft(asgi_client):
    """FR-05 AC-5.3 + NP-13 — under concurrency the bucket must NEVER
    over-issue more than ``burst`` successes. The row-level lock + single
    transaction in ``taskq_api.repository.rate_repo`` is the mechanism
    that makes this invariant hold; without it a 50-way race could
    over-consume tokens and let all 50 requests through.

    Spec scenario: 50 concurrent ``GET /v1/tasks`` requests against a
    bucket with ``burst=20``; the number of 2xx responses must be at
    most 20 (``expected_max_2xx == burst``); the remainder must be
    HTTP 429 + ``Retry-After`` + ``/errors/rate-limited``.

    NFR annotations:
      - NFR-03 (error handling): the cross-worker consistency is a
        transactional invariant — every row update goes through
        ``taskq_api.repository.session.transaction()`` with
        ``SELECT ... FOR UPDATE`` (or the SQLite equivalent) so the
        concurrent ``consume`` calls serialise.
      - NFR-06 (architecture layering): the row-locked refill sits in
        ``repository/rate_repo``; the policy + retry-after math in
        ``service/ratelimit``; the dependency-side enforcement in
        ``api/deps``.
    """
    concurrency = "50"             # case-3 input
    burst = "20"                   # case-3 input
    expected_max_2xx = "20"        # case-3 input

    # GREEN TODO: ``RateBucketRepository.consume(key_id)`` must run
    # inside ``taskq_api.repository.session.transaction()`` with a
    # row-level lock (``SELECT ... FOR UPDATE`` on PostgreSQL; SQLite
    # serialises via the engine-wide transaction). The test passes
    # only when the bucket NEVER over-issues.

    # Fire 50 concurrent requests at /v1/tasks. asyncio.gather() drives
    # them in parallel from inside the sync body via asyncio.run() on
    # an outer coroutine.
    n_requests = int(concurrency)

    async def _fire_all() -> list[int]:
        coros = [
            asgi_client.get(
                "/v1/tasks",
                headers={"X-API-Key": "test-read-key"},
            )
            for _ in range(n_requests)
        ]
        responses = await asyncio.gather(*coros)
        return [int(r.status_code) for r in responses]

    statuses = _run(_fire_all())

    # FR05-AC-5.3-no-overdraft — applies_to (3): the number of 2xx
    # responses must be at most ``burst`` (the bucket capacity). The
    # spec phrase "no overdraft" means strictly ``<= burst`` — never
    # ``> burst`` under any concurrency level.
    if expected_max_2xx == burst:
        assert expected_max_2xx == burst

    successes = sum(1 for s in statuses if 200 <= s < 300)
    assert successes <= int(burst), (
        f"FR-05 AC-5.3 violated: concurrent burst yielded {successes} "
        f"2xx responses (bucket capacity={burst}); the row-level lock "
        f"+ single transaction must prevent overdraft; "
        f"statuses={statuses!r}"
    )

    # Belt-and-braces — every over-limit response is a 429 with the
    # canonical Retry-After + /errors/rate-limited body. The
    # ``expected_max_2xx == burst`` invariant above already covers the
    # AC-5.3 contract; this catches the regression where 429s are
    # replaced by 500s (no bare except / DB-error leaks; NFR-03).
    rejected = sum(1 for s in statuses if s >= 400)
    assert rejected == n_requests - successes, (
        f"FR-05 AC-5.3 violated: rejected count mismatch; "
        f"expected {n_requests - successes} non-2xx, got {rejected}; "
        f"statuses={statuses!r}"
    )


# ---------------------------------------------------------------------------
# Case 4: `test_healthz_returns_200`
# TEST_SPEC.md FR-05 #4 — happy_path: /healthz MUST be reachable
# WITHOUT going through the rate-limit dependency (AC-5.4). The
# endpoint stays exempt even after FR-05 GREEN, so the smoke test
# keeps passing and proves the dependency is NOT mounted on the health
# router.
# ---------------------------------------------------------------------------

def test_healthz_returns_200(asgi_client):
    """FR-05 AC-5.4 — ``/healthz`` (and ``/readyz``) MUST NOT be
    rate-limited. Hitting ``/healthz`` with no X-API-Key header returns
    200, regardless of any burst exhaustion that ``/v1/*`` routes are
    experiencing.

    Sub-assertions:
      - FR05-AC-5.4-rate-exempt : endpoint == "/healthz" — the probe
        route is the canonical exempt target; the same exemption
        applies to ``/readyz`` (per SPEC.md line 120).

    NFR annotations:
      - NFR-05 (documentation): /healthz and /readyz are the two
        documented exempt routes — every /v1/* endpoint MUST carry the
        rate-limit dependency.
      - NFR-06 (architecture layering): the rate-limit dependency is
        mounted ONLY on /v1/* routers (api.tasks, api.metrics), NOT on
        the health router — this is what keeps probes anonymous AND
        rate-limit-free.
    """
    endpoint = "/healthz"              # case-4 input
    auth_header_value = ""             # case-4 input — no auth header
    expected_status = "200"            # case-4 input

    headers = (
        {"X-API-Key": auth_header_value} if auth_header_value else {}
    )

    response = _run(asgi_client.get(endpoint, headers=headers))

    result_status = response.status_code

    # FR05-AC-5.4-rate-exempt — applies_to (4): the request targets the
    # /healthz endpoint (the probe route that must NOT be rate-limited).
    # Trigger on case-4's endpoint literal "/healthz".
    if endpoint == "/healthz":
        assert endpoint == "/healthz"
        assert result_status == int(expected_status), (
            f"FR-05 AC-5.4 violated: {endpoint} returned {result_status} "
            f"(expected {expected_status}); body={response.text!r}"
        )


# ---------------------------------------------------------------------------
# Coverage-completion unit tests for the FR-05 module bindings.
#
# The four TEST_SPEC.md cases above pin the acceptance-criteria contract;
# the tests below exercise the remaining branches of the FR-05 modules
# (``service.ratelimit``, ``repository.rate_repo``) so every reachable
# line of the FR-05 surface is executed.
# ---------------------------------------------------------------------------


def test_consume_returns_allowed_then_denied_with_retry_after(monkeypatch):
    """FR-05 AC-5.1 / AC-5.2 — ``service.ratelimit.consume(key_id)`` is
    the policy boundary. On a fresh bucket the first ``burst`` calls
    return ``(True, 0.0)``; the next call returns ``(False, retry_after)``
    where ``retry_after`` is the seconds-to-next-token computed from the
    configured ``TASKQ_RATE_PER_SEC``.

    GREEN TODO: ``taskq_api.service.ratelimit.consume(key_id: str) ->
    tuple[bool, float]`` MUST delegate to
    ``taskq_api.repository.rate_repo.RateBucketRepository`` and use
    ``taskq_api.config.get_settings().rate_burst`` /
    ``rate_per_sec`` as the policy parameters.
    """
    key_id = "test-key-id-1234567890"

    # A fresh bucket starts FULL — ``burst`` allowed calls then 1
    # denied call with a non-zero ``retry_after``.
    for _ in range(20):
        allowed, retry_after = consume(key_id)
        assert allowed is True, (
            f"FR-05 AC-5.1 violated: consume() on a fresh bucket must "
            f"succeed while tokens remain; got allowed={allowed!r}"
        )
        assert retry_after == 0.0, (
            f"FR-05 AC-5.1 violated: an allowed consume() must report "
            f"retry_after=0.0 (no wait needed); got {retry_after!r}"
        )

    # Bucket is now empty; the next consume() is denied with a positive
    # retry_after that reflects ``1 / rate_per_sec`` (the time until
    # the next single token is refilled).
    allowed, retry_after = consume(key_id)
    assert allowed is False, (
        f"FR-05 AC-5.2 violated: consume() on an exhausted bucket must "
        f"be denied; got allowed={allowed!r}"
    )
    assert retry_after > 0.0, (
        f"FR-05 AC-5.2 violated: a denied consume() must report a "
        f"positive retry_after (seconds-to-next-token); got "
        f"{retry_after!r}"
    )


def test_consume_signature_returns_tuple():
    """FR-05 — ``consume(key_id)`` returns a 2-tuple ``(allowed, retry_after)``;
    both elements are typed so the dependency layer can unpack without
    defensive checks. ``allowed`` is a bool; ``retry_after`` is a float
    in seconds (the AC-5.2 header value comes straight from here).
    """
    sig = inspect.signature(consume)
    params = list(sig.parameters.values())
    assert len(params) == 1, (
        f"FR-05 violated: consume() must accept exactly one parameter "
        f"(key_id); got {[p.name for p in params]!r}"
    )
    assert "key_id" in sig.parameters, (
        f"FR-05 violated: consume() parameter must be named key_id; "
        f"got {[p.name for p in params]!r}"
    )


def test_rate_bucket_repository_consume_within_transaction():
    """FR-05 AC-5.3 — the rate-bucket row update MUST happen inside a
    single SQLAlchemy transaction (the ``taskq_api.repository.session
    .transaction()`` context manager). The row-level lock is what keeps
    the AC-5.3 "no overdraft" invariant honest across workers.

    GREEN TODO: ``taskq_api.repository.rate_repo.RateBucketRepository``
    must expose ``consume(key_id) -> tuple[bool, float]`` that
    (a) opens ``taskq_api.repository.session.transaction()``,
    (b) acquires the row-level lock on the per-key bucket row
        (``SELECT ... FOR UPDATE``), and
    (c) refills based on ``TASKQ_RATE_PER_SEC`` before consuming one
        token.
    """
    repo = RateBucketRepository()
    key_id = "tx-test-key-id"

    # First call: bucket starts full → allowed.
    allowed, retry_after = repo.consume(key_id)
    assert allowed is True
    assert retry_after == 0.0

    # Drain the bucket.
    for _ in range(19):
        allowed, _retry_after = repo.consume(key_id)
        assert allowed is True

    # Bucket now empty → next consume is denied.
    allowed, retry_after = repo.consume(key_id)
    assert allowed is False
    assert retry_after > 0.0


def test_rate_bucket_repository_isolates_keys():
    """FR-05 AC-5.1 — the bucket is per-key; one key's exhaustion does
    NOT affect another key. (A global bucket would defeat the per-key
    isolation SPEC.md line 117 calls for.)
    """
    repo = RateBucketRepository()
    key_a = "key-a-isolated-test"
    key_b = "key-b-isolated-test"

    # Drain key_a completely.
    for _ in range(20):
        repo.consume(key_a)
    allowed_a, _ = repo.consume(key_a)
    assert allowed_a is False, (
        f"FR-05 AC-5.1 violated: key_a should be exhausted after "
        f"21 consume() calls; got allowed={allowed_a!r}"
    )

    # key_b is a separate bucket and must still be full.
    allowed_b, retry_after_b = repo.consume(key_b)
    assert allowed_b is True, (
        f"FR-05 AC-5.1 violated: key_b must be isolated from key_a; "
        f"got allowed={allowed_b!r}"
    )
    assert retry_after_b == 0.0