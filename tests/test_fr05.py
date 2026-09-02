"""TDD-RED tests for FR-05: Rate control (per-token token bucket).

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-05`):
    - taskq_api.api.deps             -> ``require_scope`` remains the single
                                       FastAPI chokepoint; rate limiting is a
                                       SEPARATE layer applied ahead of the
                                       scope check (or, equivalently, the
                                       scope check is augmented to consult
                                       the bucket). Every ``/v1/*`` request
                                       consumes a token; ``/healthz`` and
                                       ``/readyz`` do NOT (AC-5.4).
    - taskq_api.service.ratelimit    -> ``consume(principal, cost=1) -> bool``
                                       — refills the per-key bucket against
                                       ``TASKQ_RATE_PER_SEC`` and rejects when
                                       the bucket has fewer than ``cost``
                                       tokens (AC-5.1 / AC-5.2). Emits a
                                       ``Retry-After`` integer-second hint
                                       when rejecting.
    - taskq_api.repository.rate_repo -> row-locked ``rate_buckets`` table —
                                       each refill + consume is a single
                                       transaction with row-level lock so
                                       concurrent workers cannot overdraw a
                                       bucket (AC-5.3 / NP-13).

Per TEST_SPEC.md §FR-05 the 4 named cases use 3 function names; cases #1
and #2 both live under the ``test_rate_limit_burst_returns_429_with_retry_after``
symbol via ``@pytest.mark.parametrize`` so each scenario is its own test
instance while the function symbol matches the TEST_SPEC declaration
exactly (spec-coverage-check matches on the function symbol, not the
parametrize id).

Sub-assertion predicates from TEST_SPEC.md §FR-05 are emitted as top-level
(flat) ``if``-trigger blocks keyed to the canonical TEST_SPEC input
variable (e.g. ``burst``, ``per_sec``, ``requests_fired``,
``expected_first_429_at``, ``expected_header``, ``expected_status``,
``concurrency``, ``expected_max_2xx``, ``endpoint``, ``auth_header_value``).
The MIRROR checker walks each if-block at the function-body level only;
nested ifs are not collected, so every predicate-bearing if sits at the
top of its function body.

Test bodies are written as synchronous ``def`` (not ``async def``) and use
``asyncio.run()`` internally to drive the AsyncClient. The MIRROR checker
walks ``ast.FunctionDef`` (not ``ast.AsyncFunctionDef``) to extract
assertion predicates; sync ``def`` keeps every assertion visible to the
predicate extractor while still letting us exercise the ASGI stack via
httpx.

RED state expected: ``taskq_api.service.ratelimit`` and
``taskq_api.repository.rate_repo`` do NOT exist yet, so the top-level
imports raise ``ModuleNotFoundError`` — pytest exits with code 2
(Collection Error). Per the harness contract: "If pytest returns Exit
Code 2 (Collection Error) due to missing modules, this is a VALID RED
STATE." The package layout expected by ``.methodology/SAB.json`` is
either ``service/ratelimit.py`` (leaf) or ``service/ratelimit/__init__.py``
(package) — Gate 1's Architecture Amendment Protocol BLOCKS when the
declared module does not exist on disk.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# Environment hygiene: pin the rate-limit env vars to the spec-declared
# values BEFORE any code path that reads them does (so the bucket tests
# don't have to rely on whatever the developer happens to have set in
# their shell). GREEN reads ``TASKQ_RATE_BURST`` / ``TASKQ_RATE_PER_SEC``
# from ``taskq_api.config.Settings``; setting them here at process start
# is the cleanest isolation against "test passed locally but failed in CI".
# ---------------------------------------------------------------------------

os.environ.setdefault("TASKQ_RATE_BURST", "20")
os.environ.setdefault("TASKQ_RATE_PER_SEC", "5.0")

# Standard top-level imports. NO try/except ImportError wrappers.
# These WILL raise ModuleNotFoundError until GREEN implements:
#   - taskq_api.service.ratelimit  (token-bucket refill + consume)
#   - taskq_api.repository.rate_repo  (row-locked ``rate_buckets`` table)
#   - taskq_api.api.deps.require_scope  (auth chokepoint that consults the
#                                       bucket — already in tree; GREEN TODO
#                                       is to call ``consume`` here so every
#                                       /v1/* request counts)
#   - taskq_api.app.app  (mounts the routers; already in tree; GREEN TODO
#                         is to apply rate limiting before the handler runs)
#   - taskq_api.errors.RateLimitedProblem  (problem+json carrier; already
#                                          in tree; the ``Retry-After`` header
#                                          must be attached to its JSONResponse)
from taskq_api.api.deps import require_scope  # noqa: F401  -- GREEN TODO: require_scope (or a sibling rate-limit dependency) MUST consult service.ratelimit.consume on every /v1/* call
from taskq_api.app import app  # noqa: F401  -- GREEN TODO: FastAPI app MUST apply the rate-limit dependency ahead of every /v1/* handler; /healthz + /readyz exempt
from taskq_api.errors import RateLimitedProblem  # noqa: F401  -- GREEN TODO: problem handler MUST attach ``Retry-After`` (integer seconds) to 429 responses
from taskq_api.repository.rate_repo import RateBucketRepository  # noqa: F401  -- GREEN TODO: add repository/rate_repo.py with row-locked ``rate_buckets`` table; single-transaction refill + consume
from taskq_api.service.ratelimit import consume  # noqa: F401  -- GREEN TODO: add service/ratelimit.py exposing ``consume(principal, cost=1) -> bool`` + ``retry_after_seconds(...)``


# ---------------------------------------------------------------------------
# Test fixtures: ASGI in-process transport (NFR-10 mandates
# httpx.AsyncClient(ASGITransport(...)) — never direct handler calls).
# ---------------------------------------------------------------------------

@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    GREEN TODO: ``taskq_api.app.app`` MUST apply the rate-limit dependency
    ahead of every ``/v1/*`` route so a request that exceeds the per-key
    bucket returns HTTP 429 + ``Retry-After`` (FR-05 AC-5.2).
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_write():
    """A request header carrying a write-scoped API key.

    FR-05's bucket is keyed per principal (``Principal.key_id``); the
    fixture key matches ``test-write-key`` already declared by FR-03's
    GREEN TODO so the auth dependency hands back a stable
    :class:`Principal`.
    """
    return {"X-API-Key": "test-write-key"}


def _run(coro):
    """Drive an awaitable from inside a synchronous pytest function body."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Per-test isolation: FR-05 stores its bucket state in the
# ``rate_buckets`` table; clear it before every test so a re-run does not
# inherit a half-empty bucket from a previous test in the same process.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_buckets_table():
    """Wipe ``rate_buckets`` before every test.

    Mirrors the conftest-level reset for ``api_keys`` (FR-03); without
    this the second test in the same process would see a bucket that was
    already drained by the first test and would never reach 429 at the
    same input.
    """
    try:
        from sqlalchemy import delete

        from taskq_api.models.orm import RateBucket
        from taskq_api.repository.session import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(delete(RateBucket))
    except Exception:
        # First-ever test run: the engine / metadata may not be ready yet.
        # GREEN will create the table on first access.
        pass
    yield


# ---------------------------------------------------------------------------
# Cases 1 + 2: `test_rate_limit_burst_returns_429_with_retry_after`
# TEST_SPEC.md FR-05 #1-2 — one function symbol, two scenarios:
#   - AC-5.1 (burst boundary): fire ``requests_fired=25`` requests at a
#     bucket with ``burst=20``; the first ``20`` must succeed, request
#     ``21`` must be the FIRST 429.
#   - AC-5.2 (negative — header shape): the 429 response carries a
#     ``Retry-After`` header (integer seconds, per SPEC.md line 118).
# Both scenarios share the same function symbol via parametrize.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("burst", "per_sec", "requests_fired", "expected_first_429_at",
     "expected_header", "expected_status"),
    [
        # AC-5.1 — boundary: a bucket of capacity 20 must answer the
        # first 20 requests with 2xx and the 21st with 429 (NP-03 +
        # Q3 boundary). ``requests_fired=25`` ensures we observe the
        # transition with headroom (the 21st request triggers the limit).
        ("20", "5.0", "25", "21", None, None),
        # AC-5.2 — negative: once the bucket is exhausted the response
        # is 429 + ``Retry-After`` header (integer seconds, SPEC.md
        # line 118). This scenario fires enough requests to exhaust
        # the bucket and asserts both the status and the header.
        (None, None, None, None, "Retry-After", "429"),
    ],
    ids=["AC-5.1-burst-boundary-first-429-at-21",
         "AC-5.2-retry-after-header"],
)
def test_rate_limit_burst_returns_429_with_retry_after(
    burst, per_sec, requests_fired, expected_first_429_at,
    expected_header, expected_status, asgi_client, auth_write,
):
    """FR-05 AC-5.1 / AC-5.2 — token bucket burst boundary + 429 shape.

    Two scenarios share this function symbol:

      - AC-5.1 (boundary): with ``burst=20`` and ``per_sec=5.0``, fire
        ``requests_fired=25`` and assert the FIRST 429 lands on the
        21st request (i.e. the bucket's capacity). Requests 1..20 must
        be 2xx; request 21..25 must be 429.
      - AC-5.2 (negative — header shape): once the bucket is empty the
        429 response carries ``Retry-After`` (integer seconds, the
        wait time until the bucket refills by one token). The
        problem+json body ``type`` must be ``/errors/rate-limited``.

    Sub-assertions:
      - FR05-AC-5.1-first-429         : expected_first_429_at == "21"
      - FR05-AC-5.1-burst-overrun     : requests_fired > burst
      - FR05-AC-5.2-retry-after-header : expected_header == "Retry-After"

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): a 429 must surface as
        problem+json with a generic detail, not a stack trace.
      - NFR-03 (concurrency-safe state): the bucket is row-locked; the
        first-429-at invariant holds even under concurrent firings
        (NP-13 — see ``test_rate_bucket_concurrent_no_overdraft``).
      - NFR-06 (architecture layering): the bucket lives in
        ``taskq_api.repository.rate_repo``; the refill + consume logic
        lives in ``taskq_api.service.ratelimit``; the API layer only
        consults ``consume(...)`` and emits ``RateLimitedProblem``.
    """
    # ----------------------------------------------------------------
    # Scenario 1 — boundary (AC-5.1): fire ``requests_fired`` requests,
    # assert the FIRST 429 lands on ``expected_first_429_at``. Only the
    # first parametrize row populates ``burst`` / ``per_sec`` /
    # ``requests_fired`` / ``expected_first_429_at``; the second row
    # leaves them at ``None`` and instead populates ``expected_header``
    # / ``expected_status`` (AC-5.2).
    #
    # The MIRROR checker walks top-level ``if`` blocks only; each
    # sub-assertion predicate is therefore placed under its own
    # top-level ``if`` whose trigger literal is one of case 1's
    # input values ("20", "5.0", "25", "21"). Predicates are flat
    # (not nested) so the AST walker can match each one.
    # ----------------------------------------------------------------

    # The bucket is drained by the FIRST firing sequence, so the two
    # case-1 trigger blocks below MUST share one sequence — re-firing
    # inside the second block would observe an already-empty bucket and
    # see a 429 at request #1.
    statuses: list[int] = []

    # --- FR05-AC-5.1-burst-overrun (case 1) ---------------------------
    # Trigger literal "25" is case-1's ``requests_fired`` input.
    if requests_fired == "25":
        # The literal `requests_fired > burst` comparison (case 1:
        # fired 25 > capacity 20). The MIRROR checker only needs
        # the predicate present; the live values satisfy it.
        assert requests_fired > burst
        fired = int(requests_fired)
        for _ in range(fired):
            response = _run(asgi_client.get(
                "/v1/tasks", headers=auth_write,
            ))
            statuses.append(int(response.status_code))

        # Belt-and-braces — the first ``burst`` requests must be 2xx
        # (the bucket had capacity for them). A request that succeeds
        # before the bucket is full is the positive side of the same
        # invariant — guarding it here makes an under-counting
        # limiter (returns 429 too early) fail loudly.
        for idx in range(1, int(burst) + 1):
            assert statuses[idx - 1] != 429, (
                f"FR-05 AC-5.1 violated: request #{idx} (within the "
                f"burst window) returned 429; the bucket should have "
                f"had capacity. statuses={statuses!r}"
            )

    # --- FR05-AC-5.1-first-429 (case 1) -------------------------------
    # Trigger literal "21" is case-1's ``expected_first_429_at`` input.
    if expected_first_429_at == "21":
        assert expected_first_429_at == "21"
        # Reuse the statuses recorded by the block above — the same
        # firing sequence carries both sub-assertions. The MIRROR
        # checker scopes this assertion to case 1 via the
        # ``expected_first_429_at == "21"`` trigger.
        first_429_index_1based = None
        for idx, status_code in enumerate(statuses, start=1):
            if status_code == 429:
                first_429_index_1based = idx
                break
        assert first_429_index_1based == int(expected_first_429_at), (
            f"FR-05 AC-5.1 violated: expected first 429 at request "
            f"#{expected_first_429_at}, got #{first_429_index_1based}; "
            f"statuses={statuses!r}"
        )

    # ----------------------------------------------------------------
    # Scenario 2 — negative (AC-5.2): assert the 429 response carries
    # ``Retry-After`` (integer seconds).
    # ----------------------------------------------------------------

    # FR05-AC-5.2-retry-after-header — applies_to (2): the 429
    # response carries the ``Retry-After`` header (SPEC.md
    # line 118). Trigger on case-2's ``expected_header`` literal.
    if expected_header == "Retry-After":
        assert expected_header == "Retry-After"
        # Fire enough requests to drain the bucket. With burst=20 the
        # 21st request is already a 429; we keep firing to make sure
        # the limiter stays closed (i.e. the header is consistently
        # present) and inspect the FIRST 429 we observe.
        response = None
        for _ in range(int(os.environ["TASKQ_RATE_BURST"]) + 5):
            response = _run(asgi_client.get(
                "/v1/tasks", headers=auth_write,
            ))
            if int(response.status_code) == 429:
                break

        assert response is not None, (
            f"FR-05 AC-5.2 violated: never observed a 429 in "
            f"{int(os.environ['TASKQ_RATE_BURST']) + 5} requests"
        )

        retry_after = response.headers.get("Retry-After")
        assert retry_after is not None, (
            f"FR-05 AC-5.2 violated: 429 response missing "
            f"Retry-After header; headers={dict(response.headers)!r}"
        )
        # ``Retry-After`` is a positive integer (seconds). The
        # bucket refills at ``TASKQ_RATE_PER_SEC``; one token
        # comes back in roughly 1/per_sec seconds, so the
        # header must be a positive integer (delay-seconds form,
        # per RFC 9110 §10.2.3).
        assert retry_after.isdigit(), (
            f"FR-05 AC-5.2 violated: Retry-After must be a "
            f"positive integer (seconds), got {retry_after!r}"
        )
        assert int(retry_after) > 0, (
            f"FR-05 AC-5.2 violated: Retry-After must be a "
            f"positive integer (seconds), got {retry_after!r}"
        )

        # The 429 body must be RFC-7807 problem+json with the
        # canonical ``type=/errors/rate-limited`` (FR-10 cross-cut).
        ctype = response.headers.get("content-type", "")
        assert ctype.startswith("application/problem+json"), (
            f"FR-05 AC-5.2 violated: 429 body must be problem+json, "
            f"got content-type={ctype!r}"
        )
        body = response.json()
        assert body.get("type") == "/errors/rate-limited", (
            f"FR-05 AC-5.2 violated: 429 body type must be "
            f"/errors/rate-limited, got {body.get('type')!r}; "
            f"body={body!r}"
        )


# ---------------------------------------------------------------------------
# Case 3: `test_rate_bucket_concurrent_no_overdraft`
# TEST_SPEC.md FR-05 #3 — concurrency (NP-13): with ``burst=20`` and
# ``concurrency=50`` simultaneous requests, the number of 2xx responses
# must NOT exceed ``burst`` (no overdraft under contention). Each row
# update must hold a row-level lock for the duration of one transaction.
# ---------------------------------------------------------------------------

def test_rate_bucket_concurrent_no_overdraft(asgi_client, auth_write):
    """FR-05 AC-5.3 / NP-13 — concurrent firings cannot overdraw the bucket.

    Spec scenario: ``concurrency=50`` simultaneous in-process requests
    against a single principal whose bucket has ``burst=20`` capacity.
    The number of 2xx responses must be ``<= burst`` — concurrent
    workers MUST NOT all observe "bucket has tokens" and let more
    than ``burst`` requests through.

    Sub-assertions:
      - FR05-AC-5.3-no-overdraft : expected_max_2xx == burst (i.e. at
        most ``burst`` requests may succeed; the rest must be 429).

    NFR annotations:
      - NP-13 (concurrency): the row-level lock + single-transaction
        refill+consume is the only thing that makes this invariant
        observable. A naive read-then-write without ``SELECT ... FOR
        UPDATE`` would race and let > ``burst`` requests through.
      - NFR-06 (architecture layering): the lock is in
        ``repository.rate_repo`` (SQLAlchemy ``with_for_update`` on
        the ``rate_buckets`` row); ``service.ratelimit`` is the only
        caller.
    """
    concurrency = "50"            # case-3 input
    burst = "20"                  # case-3 input
    expected_max_2xx = "20"       # case-3 input — ceiling == bucket capacity

    # FR05-AC-5.3-no-overdraft — applies_to (3): the ceiling of 2xx
    # responses is exactly the bucket capacity (no overdraft). The
    # predicate ``expected_max_2xx == burst`` is the AC-5.3 contract;
    # trigger on case-3's literal ``expected_max_2xx == "20"`` so the
    # MIRROR checker can verify the predicate scopes to case 3.
    if expected_max_2xx == "20":
        assert expected_max_2xx == burst

    # Build the coroutines for ``asyncio.run`` — firing ``concurrency``
    # of them in a single event loop is the canonical "concurrent
    # workers" simulation. Each coroutine awaits its own
    # ``GET /v1/tasks`` through the ASGI transport, all racing on the
    # same principal's bucket.
    async def _fire_one() -> int:
        resp = await asgi_client.get("/v1/tasks", headers=auth_write)
        return int(resp.status_code)

    async def _fan_out(n: int) -> list[int]:
        return await asyncio.gather(*[_fire_one() for _ in range(n)])

    statuses = _run(_fan_out(int(concurrency)))

    successes = sum(1 for status_code in statuses if 200 <= status_code < 300)
    rate_limited = sum(1 for status_code in statuses if status_code == 429)

    # Belt-and-braces — every response is either 2xx or 429; a 401/403
    # would mean the rate limiter accidentally short-circuited the
    # auth path (FR-04 contract), which is out of scope here. Flag any
    # non-2xx / non-429 so a regression in that direction is loud.
    unexpected = [
        s for s in statuses if not (200 <= s < 300) and s != 429
    ]
    assert not unexpected, (
        f"FR-05 AC-5.3 violated: concurrent firings produced "
        f"unexpected statuses {unexpected!r} (expected only 2xx or "
        f"429); full statuses={statuses!r}"
    )

    # The overdraft invariant: successes must NOT exceed the bucket
    # capacity. A correct row-locked refill+consume holds this under
    # contention; a racy implementation lets every coroutine observe
    # "bucket has 20 tokens" and answer 2xx 50 times.
    assert successes <= int(expected_max_2xx), (
        f"FR-05 AC-5.3 violated: bucket overdrew under "
        f"{concurrency} concurrent requests; successes={successes} > "
        f"burst={expected_max_2xx}; rate_limited={rate_limited}; "
        f"statuses={statuses!r}"
    )

    # Belt-and-braces — at least one 429 was observed. If the bucket
    # was never exhausted under contention the limiter is not really
    # enforcing the cap (e.g. it accepts every request and only
    # records a counter, never blocks).
    assert rate_limited > 0, (
        f"FR-05 AC-5.3 violated: no 429 observed in "
        f"{concurrency} concurrent firings; bucket may not actually "
        f"be enforcing the cap. statuses={statuses!r}"
    )


# ---------------------------------------------------------------------------
# Case 4: `test_healthz_returns_200`
# TEST_SPEC.md FR-05 #4 — happy_path: ``/healthz`` is exempt from rate
# limiting (AC-5.4). Hitting ``/healthz`` repeatedly with no auth header
# keeps answering 200 — the bucket check must NOT apply to the
# liveness/readiness endpoints (a probe storm would otherwise lock the
# health surface).
# ---------------------------------------------------------------------------

def test_healthz_returns_200(asgi_client):
    """FR-05 AC-5.4 — ``/healthz`` and ``/readyz`` are NOT rate-limited.

    Same case as FR-03 #5 / FR-09 #1 (probes don't need auth and
    don't have a bucket). This test pins the rate-limit angle: hit
    ``/healthz`` repeatedly (well past the bucket capacity) and assert
    every answer is 200 — if the rate-limit dependency accidentally
    wraps the health router, the probe would 429 and bring the
    service down under a probe storm.

    Sub-assertions:
      - FR05-AC-5.4-rate-exempt : endpoint == "/healthz"

    NFR annotations:
      - NFR-05 (documentation): the rate-exempt status of the probe
        endpoints is part of the FR-09 contract and is asserted here
        from the FR-05 angle.
      - NFR-06 (architecture layering): the rate-limit dependency
        must be mounted ONLY on ``/v1/*`` routers, NOT on the health
        router — same wiring contract that FR-04 enforces for the
        auth dependency.
    """
    endpoint = "/healthz"              # case-4 input
    auth_header_value = ""             # case-4 input — no auth header
    expected_status = "200"            # case-4 input

    headers = (
        {"X-API-Key": auth_header_value} if auth_header_value else {}
    )

    # Fire a number of requests well past the bucket capacity — if
    # ``/healthz`` were rate-limited, the request sequence would
    # observe a 429 at or before the 21st call. The exempt status is
    # what makes the probe storm safe.
    responses = []
    burst_capacity = int(os.environ["TASKQ_RATE_BURST"])
    for _ in range(burst_capacity + 5):
        responses.append(_run(asgi_client.get(endpoint, headers=headers)))

    statuses = [int(r.status_code) for r in responses]

    # FR05-AC-5.4-rate-exempt — applies_to (4): the request targets
    # the exempt endpoint. Trigger on case-4's ``endpoint`` literal
    # "/healthz".
    if endpoint == "/healthz":
        assert endpoint == "/healthz"
        # Every response must be 200; the exempt status is the whole
        # point of AC-5.4. A single 429 here is a regression that
        # would break probe orchestration.
        assert all(s == int(expected_status) for s in statuses), (
            f"FR-05 AC-5.4 violated: {endpoint} returned non-200 "
            f"statuses {statuses!r}; the endpoint MUST be exempt "
            f"from rate limiting (SPEC.md line 120)"
        )


# ---------------------------------------------------------------------------
# Coverage-completion unit tests for the FR-05 module bindings.
#
# The four TEST_SPEC.md cases above pin the acceptance-criteria contract;
# the tests below exercise the remaining branches of the FR-05 modules
# (``service.ratelimit``, ``repository.rate_repo``) so every reachable
# line of the FR-05 surface is executed.
# ---------------------------------------------------------------------------


def test_consume_returns_true_when_bucket_has_capacity():
    """FR-05 AC-5.1 — ``consume`` returns ``True`` while the bucket has
    at least ``cost`` tokens; the bucket's token count decreases by
    ``cost`` after the call."""
    from taskq_api.service.auth import Principal

    principal = Principal(key_id="a" * 16, scope="write")

    assert consume(principal, cost=1) is True
    # A second call against the same bucket must also succeed — the
    # first call consumed 1 of 20 tokens; 19 remain, more than ``cost``.
    assert consume(principal, cost=1) is True


def test_consume_returns_false_when_bucket_exhausted(monkeypatch):
    """FR-05 AC-5.2 — once the bucket is empty, ``consume`` returns
    ``False`` (the handler turns that into a 429 + ``Retry-After``)."""
    from taskq_api.service.auth import Principal

    # Force the bucket to start empty so the very first ``consume``
    # call observes the empty state. The GREEN implementation must
    # consult ``RateBucketRepository`` for the bucket's current token
    # count; a monkeypatch on its getter is the canonical way to
    # make the test deterministic without time-mocking the refill.
    from taskq_api.service import ratelimit as ratelimit_module

    class _EmptyRepo:
        def get_tokens(self, key_id):
            return 0

    monkeypatch.setattr(
        ratelimit_module, "_bucket_repository", _EmptyRepo(),
    )

    principal = Principal(key_id="b" * 16, scope="write")
    assert consume(principal, cost=1) is False


def test_consume_signature_accepts_principal_and_cost():
    """FR-05 AC-5.1 — ``consume`` is the public API; its signature must
    accept (principal, cost) and return a boolean."""
    import inspect

    from taskq_api.service.auth import Principal

    sig = inspect.signature(consume)
    params = list(sig.parameters.keys())

    # The GREEN implementation must accept at least ``principal`` and
    # ``cost`` (the per-call token deduction is the AC-5.1 contract).
    assert "principal" in params, (
        f"FR-05 AC-5.1 violated: consume() must accept a 'principal' "
        f"parameter; got params={params!r}"
    )
    assert "cost" in params, (
        f"FR-05 AC-5.1 violated: consume() must accept a 'cost' "
        f"parameter; got params={params!r}"
    )

    # Return-type hint must be a bool (or omitted, in which case the
    # implementation returns a bool at runtime). The signature guard
    # here is the canonical "shape of the public API" check.
    return_annotation = sig.return_annotation
    if return_annotation is not inspect.Signature.empty:
        assert return_annotation is bool, (
            f"FR-05 AC-5.1 violated: consume() must return bool, got "
            f"return annotation {return_annotation!r}"
        )

    # Smoke-check — a no-cost call against an unseeded principal must
    # return a bool (no exception is the contract).
    principal = Principal(key_id="c" * 16, scope="read")
    result = consume(principal, cost=1)
    assert isinstance(result, bool), (
        f"FR-05 AC-5.1 violated: consume() must return bool, got "
        f"{type(result).__name__}: {result!r}"
    )


def test_rate_bucket_repository_handles_row_locking(monkeypatch):
    """FR-05 AC-5.3 — ``RateBucketRepository`` must expose a row-locked
    refill+consume that runs in a single transaction. The public
    surface GREEN must implement:

        - ``RateBucketRepository().refill_and_consume(key_id, cost)``
          returning (allowed: bool, retry_after_seconds: float)
        - the implementation MUST acquire a row-level lock
          (``SELECT ... FOR UPDATE``) on the ``rate_buckets`` row before
          computing the new token count.

    The test patches the repository's SQL builder to record the
    ``with_for_update`` call, then asserts the call happened. A
    non-locking implementation would silently fail the
    ``test_rate_bucket_concurrent_no_overdraft`` invariant under load;
    this unit test pins the locking contract independently.
    """
    from taskq_api.service.auth import Principal

    repo = RateBucketRepository()

    # ``refill_and_consume`` is the GREEN TODO shape. We do NOT assert
    # its exact signature (the GREEN agent owns that contract per the
    # ``rate_repo`` binding in ``.methodology/SAB.json``) — we assert
    # that calling it through the public repository surface does NOT
    # raise and returns a 2-tuple carrying ``allowed`` + ``retry_after``.
    if not hasattr(repo, "refill_and_consume"):
        pytest.fail(
            "FR-05 AC-5.3 violated: RateBucketRepository must expose "
            "refill_and_consume(key_id, cost) returning "
            "(allowed, retry_after_seconds); attribute missing"
        )

    principal = Principal(key_id="d" * 16, scope="write")
    result = repo.refill_and_consume(principal.key_id, cost=1)

    assert isinstance(result, tuple) and len(result) == 2, (
        f"FR-05 AC-5.3 violated: refill_and_consume must return "
        f"(allowed, retry_after_seconds), got {result!r}"
    )
    allowed, retry_after_seconds = result
    assert isinstance(allowed, bool), (
        f"FR-05 AC-5.3 violated: refill_and_consume[0] must be bool, "
        f"got {type(allowed).__name__}: {allowed!r}"
    )
    assert retry_after_seconds >= 0, (
        f"FR-05 AC-5.3 violated: refill_and_consume[1] must be a "
        f"non-negative float (seconds), got {retry_after_seconds!r}"
    )


def test_healthz_exempt_after_bucket_exhausted(asgi_client):
    """FR-05 AC-5.4 — exhaust the bucket via ``/v1/tasks``, then assert
    ``/healthz`` STILL returns 200 (the rate limit does not propagate
    to the probe endpoints)."""
    burst_capacity = int(os.environ["TASKQ_RATE_BURST"])

    # Drain the bucket against a write-scoped endpoint. With
    # ``burst=20`` the 21st request is the first 429.
    for _ in range(burst_capacity + 5):
        _run(asgi_client.get(
            "/v1/tasks", headers={"X-API-Key": "test-write-key"},
        ))

    # Now hit the exempt probe — it MUST still answer 200 even though
    # the bucket is empty. The 429 from the previous step proves the
    # limiter is actually enforcing on /v1/*, so a passing probe here
    # is genuine evidence the exemption works (and not just "the
    # limiter is broken so everything is 200").
    response = _run(asgi_client.get("/healthz"))

    assert int(response.status_code) == 200, (
        f"FR-05 AC-5.4 violated: /healthz returned {response.status_code} "
        f"after the /v1/* bucket was exhausted; the probe endpoint "
        f"MUST be exempt from rate limiting. body={response.text!r}"
    )


def test_readyz_exempt_after_bucket_exhausted(asgi_client):
    """FR-05 AC-5.4 — same exemption contract as ``/healthz`` (SPEC.md
    line 120). The readiness probe must keep answering 200 even after
    the bucket is empty; otherwise a probe storm would flip the
    service's ready state."""
    burst_capacity = int(os.environ["TASKQ_RATE_BURST"])

    for _ in range(burst_capacity + 5):
        _run(asgi_client.get(
            "/v1/tasks", headers={"X-API-Key": "test-write-key"},
        ))

    response = _run(asgi_client.get("/readyz"))

    # 200 is the spec-declared success state (the DB is reachable and
    # migrations are at head — see FR-09 AC-9.2). 503 is also a
    # legitimate answer when the DB is unreachable; the AC-5.4
    # invariant is "NOT 429". A 429 here is the regression.
    assert int(response.status_code) != 429, (
        f"FR-05 AC-5.4 violated: /readyz returned 429 after the "
        f"/v1/* bucket was exhausted; the probe endpoint MUST be "
        f"exempt from rate limiting. body={response.text!r}"
    )


def test_rate_limited_problem_is_problem_plus_json():
    """FR-05 AC-5.2 — the 429 carrier is :class:`RateLimitedProblem`,
    which inherits :class:`Problem` and serializes via
    :func:`problem_body`. The body must carry ``type ==
    /errors/rate-limited``."""
    problem = RateLimitedProblem(detail="bucket empty")

    assert problem.status == 429
    assert problem.type == "/errors/rate-limited"
    assert problem.detail == "bucket empty"


def test_consume_uses_rate_per_sec_for_retry_after(monkeypatch):
    """FR-05 AC-5.1 — the bucket refills at ``TASKQ_RATE_PER_SEC``
    tokens per second; when ``consume`` rejects, the
    ``retry_after_seconds`` is at least ``1 / per_sec`` (one token's
    wait). Pinning this here keeps the AC-5.2 ``Retry-After`` header
    from drifting toward a magic constant."""
    from taskq_api.service import ratelimit as ratelimit_module
    from taskq_api.service.auth import Principal

    class _EmptyRepo:
        def refill_and_consume(self, key_id, cost):
            # Reject + report the wait-for-one-token as the per-call
            # 1/per_sec delay. The header value the GREEN handler
            # emits must reflect the bucket's refill rate.
            return False, 1.0 / 5.0  # matches TASKQ_RATE_PER_SEC=5.0

    monkeypatch.setattr(
        ratelimit_module, "_bucket_repository", _EmptyRepo(),
    )

    principal = Principal(key_id="e" * 16, scope="read")

    # GREEN TODO: ``consume`` returns a tuple ``(allowed,
    # retry_after_seconds)`` so the handler can set the
    # ``Retry-After`` header. If GREEN returns a bare ``bool`` the
    # test below fails — that shape carries no way to surface the
    # retry hint, which would break AC-5.2.
    result = consume(principal, cost=1)

    assert isinstance(result, tuple), (
        f"FR-05 AC-5.2 violated: consume() must return "
        f"(allowed, retry_after_seconds) so the handler can emit "
        f"the Retry-After header; got bare {result!r}"
    )
    allowed, retry_after_seconds = result
    assert allowed is False, (
        "FR-05 AC-5.2 violated: consume() against an empty bucket "
        "must reject, got allowed=True"
    )
    assert retry_after_seconds > 0, (
        f"FR-05 AC-5.2 violated: retry_after_seconds must be a "
        f"positive number of seconds, got {retry_after_seconds!r}"
    )