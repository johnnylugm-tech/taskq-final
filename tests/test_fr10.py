"""TDD-RED tests for FR-10: RFC 7807 error contract.

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-10`):
    - taskq_api.errors  -> RFC-7807 ``Problem`` base class + the
                          seven concrete subclasses (``ValidationProblem``,
                          ``UnauthenticatedProblem``, ``ForbiddenProblem``,
                          ``NotFoundProblem``, ``ConflictProblem``,
                          ``RateLimitedProblem``) plus the canonical
                          ``problem_body(...)`` wire serializer.
                          ``type`` (URI), ``title``, ``status``,
                          ``detail``, ``instance``, ``correlation_id``
                          are the AC-10.2 contract fields.
    - taskq_api.app     -> FastAPI composition root that registers the
                          ``Problem`` exception handler so every error
                          class is rendered as
                          ``Content-Type: application/problem+json``
                          (AC-10.1). The handler also stamps the
                          ``X-Correlation-Id`` response header
                          (AC-10.4).

Per TEST_SPEC.md FR-10 the 5 named cases use 3 function names; cases #1,
#2, and #3 share ``test_422_404_429_all_problem_json`` via
``@pytest.mark.parametrize`` so each scenario is its own pytest test
instance while the function symbol matches the TEST_SPEC declaration
exactly (spec-coverage-check matches on the function symbol, not the
parametrize id).

Sub-assertion predicates from TEST_SPEC.md §FR-10 are emitted as
top-level (flat) ``if``-trigger blocks keyed to the canonical TEST_SPEC
input variable (e.g. ``scenario``, ``expected_status``, ``content_type``,
``injected_exception_class``, ``forbidden_pattern``, ``expected_hits``,
``header_name``, ``log_record_name``, ``expected_match``). The MIRROR
checker walks each if-block at the function-body level only; nested ifs
are not collected, so every predicate-bearing if sits at the top of its
function body.

Test bodies are synchronous ``def`` (not ``async def``); the MIRROR
checker walks ``ast.FunctionDef`` (not ``ast.AsyncFunctionDef``) so each
predicate-bearing if must be reachable as a top-level statement of the
sync body. ``asyncio.run()`` drives the ``AsyncClient`` from inside the
sync body.

RED state expected: ``taskq_api.errors`` and ``taskq_api.app`` ARE present
in tree (per `.methodology/SAB.json` the FR-10 binding is exactly those
two modules), but the AC-10.1 ``Content-Type: application/problem+json``
contract for 422 / 404 / 429 is exercised in-process below. For the 429
scenario specifically, the rate-limit handler at ``service.ratelimit`` is
already in tree but the exception class raised on bucket exhaustion is
not yet ``RateLimitedProblem`` — the test pins the AC-10.1 contract that
the eventual GREEN must satisfy. The ``test_500_detail_has_no_stack_trace``
case fails because the AC-10.3 "no Traceback in detail" invariant is
not yet enforced by the exception handler chain — the eventual GREEN
must catch unhandled exceptions, redact stack traces from the body, and
emit a sanitized 500 response. The ``test_correlation_id_in_header_and_log``
case fails because the AC-10.4 "X-Correlation-Id appears in BOTH
response header AND server log" invariant is not yet wired end-to-end.

Citations:
    - SPEC.md lines 162-168 (FR-10 AC list)
    - SPEC.md line 167 (AC-10.5 error-code map)
    - SAD.md §2.4 (RFC 7807 wire contract)
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

# Standard top-level imports. NO try/except ImportError wrappers.
# Per `.methodology/SAB.json` the FR-10 binding is exactly:
#   - taskq_api.errors  (Problem classes + wire serializer)
#   - taskq_api.app     (FastAPI composition root + exception handlers)
# Both modules ARE present in tree, so the imports succeed at collection
# time; the assertion-level RED state is the AC-10.1 / AC-10.3 /
# AC-10.4 wire contract.
from taskq_api.errors import (  # noqa: F401  -- GREEN TODO: confirm Problem class public API
    NotFoundProblem,
    Problem,
    RateLimitedProblem,
    ValidationProblem,
    problem_body,
)
from taskq_api.app import app  # noqa: F401  -- GREEN TODO: confirm Problem exception handler mounts every concrete subclass as application/problem+json


# ---------------------------------------------------------------------------
# Test fixtures: ASGI in-process transport (NFR-10 mandates
# httpx.AsyncClient(ASGITransport(...)) — never direct handler calls).
# ---------------------------------------------------------------------------

@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    GREEN TODO: ``taskq_api.app.app`` MUST mount every Problem subclass
    raised by ``/v1/*`` routes through the AC-10.1 problem+json handler
    so 422 / 404 / 429 all carry the canonical wire shape.

    ``raise_app_exceptions=False`` lets the AC-10.3 test inspect the
    raw 500 body the server would emit when no general
    ``Exception`` handler is registered (the current RED state) —
    without this, httpx would re-raise the ValueError before we can
    check that the body contains no Traceback.
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_write():
    """A request header carrying a write-scoped API key.

    FR-10 AC-10.5 + FR-04 AC-4.1: the validation (422), not-found (404),
    and rate-limited (429) responses are observed through the
    /v1/* surface; that surface requires a key with at least
    ``write`` scope. The fixture key is the same ``test-write-key``
    declared by FR-03's GREEN TODO in ``taskq_api.service.auth``.
    """
    return {"X-API-Key": "test-write-key"}


def _run(coro):
    """Drive an awaitable from inside a synchronous pytest function body."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers — exercise the wire serializer directly for the 422 / 404 / 429
# error classes. The HTTP path is the canonical surface; these helpers
# let the test assert the AC-10.2 field set without depending on a
# fully-wired FastAPI route (NFR-10 still relies on the HTTP tests below).
# ---------------------------------------------------------------------------


def _wire_shape_for(problem: Problem) -> dict:
    """Return the JSON body the problem+json handler would render for
    ``problem``. Pinned to ``taskq_api.errors.problem_body`` so the
    test stays valid across GREEN-side refactors of the serializer.
    """
    # GREEN TODO: ``taskq_api.errors.problem_body`` MUST emit
    # {type, title, status, detail, instance, correlation_id} —
    # AC-10.2 wire contract.
    return problem_body(problem)


# ---------------------------------------------------------------------------
# Cases 1-3: `test_422_404_429_all_problem_json`
# TEST_SPEC.md FR-10 #1-3 — one function symbol, three scenarios:
#   - AC-10.1 + AC-10.2 (validation): POST /v1/tasks with an empty
#     ``command`` field yields 422 + application/problem+json whose
#     body carries type/title/status/detail (the AC-10.2 wire contract).
#   - AC-10.5 (not_found): GET /v1/tasks/<unknown-uuid> yields 404 +
#     application/problem+json.
#   - AC-10.5 (rate_limited): a burst that exceeds the bucket cap
#     yields 429 + application/problem+json.
# All three scenarios share one function symbol via parametrize so each
# scenario is its own pytest test instance while the function symbol
# matches the TEST_SPEC declaration exactly.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("scenario", "expected_status", "content_type"),
    [
        # AC-10.1 + AC-10.2 — validation. POSTing a malformed body to a
        # /v1/* endpoint yields 422 + problem+json (Q2, NP-04).
        ("validation", "422", "application/problem+json"),
        # AC-10.5 — not_found. GET on an unknown task id yields 404 +
        # problem+json (Q5, NP-04).
        ("not_found", "404", "application/problem+json"),
        # AC-10.5 — rate_limited. Burst that overruns the bucket yields
        # 429 + problem+json (Q5, NP-04).
        ("rate_limited", "429", "application/problem+json"),
    ],
    ids=["AC-10.1-validation-422",
         "AC-10.5-not-found-404",
         "AC-10.5-rate-limited-429"],
)
# NFR-04 (sensitive data redaction), NFR-06 (architecture_constraints), NFR-10 (integration_coverage)
def test_422_404_429_all_problem_json(
    scenario, expected_status, content_type,
    asgi_client, auth_write, monkeypatch,
):
    """FR-10 AC-10.1 + AC-10.5 — every non-2xx response from a /v1/*
    endpoint MUST be ``Content-Type: application/problem+json`` with the
    canonical AC-10.2 wire shape (``type``, ``title``, ``status``,
    ``detail``, ``instance``, ``correlation_id``).

    Three scenarios share this function symbol:

      - validation (422): a request body that violates a schema rule
        (e.g. empty ``command``) yields ``ValidationProblem`` whose
        exception handler renders 422 + problem+json.

      - not_found (404): looking up a task id that does not exist
        yields ``NotFoundProblem`` → 404 + problem+json.

      - rate_limited (429): a burst that overruns the token bucket
        yields ``RateLimitedProblem`` → 429 + problem+json (the FR-05
        rate-limit cross-cut).

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): problem+json is the only
        acceptable wire shape for non-2xx responses — a stack-trace-
        leaking 500 must NOT surface a Python traceback.
      - NFR-06 (architecture layering): the exception handler lives in
        ``taskq_api.app`` (composition root) and delegates serialization
        to ``taskq_api.errors.problem_body`` (independence module).
      - NFR-10 (integration_coverage): the assertion runs through the
        real ASGI stack via httpx — no direct handler call.
    """

    if scenario == "validation":
        # AC-10.1 + AC-10.2 — validation. POST a task whose ``command``
        # field is empty; the FR-01 schema rejects it with 422.
        # GREEN TODO: ``taskq_api.api.tasks.create_task`` MUST raise
        # ``taskq_api.errors.ValidationProblem`` (status=422, type=
        # "/errors/validation") when the body fails schema validation;
        # the AC-10.1 problem+json handler then renders 422 +
        # application/problem+json.
        response = _run(asgi_client.post(
            "/v1/tasks",
            headers=auth_write,
            json={"name": "bad", "command": ""},
        ))

    elif scenario == "not_found":
        # AC-10.5 — not_found. Look up a task id that the repository
        # has never seen. The FR-01 lookup must raise
        # ``NotFoundProblem`` (status=404, type="/errors/not-found").
        response = _run(asgi_client.get(
            "/v1/tasks/00000000-0000-0000-0000-000000000000",
            headers=auth_write,
        ))

    elif scenario == "rate_limited":
        # AC-10.5 — rate_limited. Force the bucket to deny and verify
        # that the FR-05 handler raises ``RateLimitedProblem``
        # (status=429, type="/errors/rate-limited") which the AC-10.1
        # handler turns into 429 + problem+json.
        #
        # GREEN TODO: ``taskq_api.service.ratelimit`` MUST raise
        # ``taskq_api.errors.RateLimitedProblem`` (status=429,
        # retry_after=integer seconds) on bucket exhaustion — and the
        # ``taskq_api.app`` exception handler MUST render it as
        # application/problem+json with the Retry-After header set.
        from taskq_api import service as _service_module

        def _always_denied(*args, **kwargs):
            return False, 1.0

        monkeypatch.setattr(
            _service_module.ratelimit, "try_consume", _always_denied,
        )

        response = _run(asgi_client.get(
            "/v1/tasks",
            headers=auth_write,
        ))

    else:
        # Defensive — every parametrize case is enumerated above.
        pytest.fail(f"unhandled scenario: {scenario!r}")

    result_status = response.status_code

    # FR10-AC-10.1-validation-status — applies_to (1): the validation
    # scenario must answer 422. Trigger on case-1's expected_status
    # literal "422".
    if expected_status == "422":
        assert expected_status == "422"
        assert result_status == int(expected_status), (
            f"FR-10 AC-10.1 violated: scenario={scenario!r} returned "
            f"{result_status} (expected {expected_status}); "
            f"body={response.text!r}"
        )

    # FR10-AC-10.1-content-type — applies_to (1, 2, 3): the body MUST
    # carry ``Content-Type: application/problem+json``. Trigger on
    # case-1/2/3's content_type literal.
    if content_type == "application/problem+json":
        assert content_type == "application/problem+json"
        actual_ctype = response.headers.get("content-type", "")
        assert actual_ctype.startswith("application/problem+json"), (
            f"FR-10 AC-10.1 violated: scenario={scenario!r} body must be "
            f"application/problem+json, got content-type={actual_ctype!r}; "
            f"body={response.text!r}"
        )

    # FR10-AC-10.2-not-found-status — applies_to (2): the not_found
    # scenario must answer 404. Trigger on case-2's expected_status
    # literal "404".
    if expected_status == "404":
        assert expected_status == "404"
        assert result_status == int(expected_status), (
            f"FR-10 AC-10.5 violated: scenario={scenario!r} returned "
            f"{result_status} (expected {expected_status}); "
            f"body={response.text!r}"
        )

    # FR10-AC-10.5-rate-limit-status — applies_to (3): the
    # rate_limited scenario must answer 429. Trigger on case-3's
    # expected_status literal "429".
    if expected_status == "429":
        assert expected_status == "429"
        assert result_status == int(expected_status), (
            f"FR-10 AC-10.5 violated: scenario={scenario!r} returned "
            f"{result_status} (expected {expected_status}); "
            f"body={response.text!r}"
        )

    # AC-10.2 wire-shape guard — every problem+json body MUST carry
    # the canonical fields (type / title / status / detail — instance +
    # correlation_id are optional on the wire but are populated by the
    # handler). This assertion catches a handler that emits e.g. only
    # ``detail`` without the type/title/status fields.
    body = response.json()
    for required_field in ("type", "title", "status", "detail"):
        assert required_field in body, (
            f"FR-10 AC-10.2 violated: scenario={scenario!r} body is "
            f"missing required field {required_field!r}; body={body!r}"
        )


# ---------------------------------------------------------------------------
# Case 4: `test_500_detail_has_no_stack_trace`
# TEST_SPEC.md FR-10 #4 — security: when an unhandled exception bubbles
# up to the FastAPI exception handler chain, the AC-10.3 invariant
# "detail 不得含 SQL 陳述、堆疊追蹤、檔案路徑、資料庫結構描述"
# MUST hold — the body's ``detail`` (and the whole body) MUST NOT
# contain the literal ``Traceback`` substring, nor any
# ``/path/to/file.py`` or ``SELECT ...`` leak.
# ---------------------------------------------------------------------------

# NFR-02 (security: no stack-trace disclosure), NFR-04 (sensitive data redaction), NFR-06 (architecture_constraints)
def test_500_detail_has_no_stack_trace(asgi_client, auth_write, monkeypatch):
    """FR-10 AC-10.3 — ``detail`` MUST NOT leak internal details:
    forbidden substrings are ``Traceback`` (stack trace marker), any
    absolute path under ``/Users/...`` (filesystem leak), and any
    ``SELECT ...`` SQL fragment. The forbidden_pattern literal from the
    spec is ``"Traceback"``; the test additionally scans the body for
    ``forbidden_pattern`` to make the failure mode obvious.

    Spec scenario: an endpoint raises an unhandled ``ValueError`` (the
    injected exception class from TEST_SPEC case #4). The AC-10.3
    invariant requires the eventual 500 body to be sanitized — only a
    generic ``"internal server error"`` message may appear; no
    ``Traceback``, no filesystem path, no SQL.

    NFR annotations:
      - NFR-02 (HTTP & data-layer security): a 500 body that contains
        a Python traceback is a textbook info-disclosure vulnerability
        (SEC T-10). The RED state (current handler lets the traceback
        through) is exactly what this test catches.
      - NFR-04 (sensitive data redaction): the redaction surface in
        ``taskq_api.errors.redact_secrets`` is the upstream of this
        contract — a 500 that omits stack trace MUST also omit any
        ``postgres://...`` connection string that the traceback would
        have surfaced.
      - NFR-06 (architecture layering): the sanitization belongs in
        the composition-root exception handler (``taskq_api.app``);
        the independence module (``taskq_api.errors``) only owns the
        serializer, not the policy.
    """
    injected_exception_class = "ValueError"  # case-4 input
    forbidden_pattern = "Traceback"          # case-4 input
    expected_hits = "0"                      # case-4 input

    # Force a /v1/* route to raise an unhandled ``ValueError`` so the
    # exception handler chain turns it into a 500 response.
    #
    # GREEN TODO: ``taskq_api.app`` MUST register an
    # ``@application.exception_handler(Exception)`` (or equivalent) that
    # catches unhandled exceptions, renders a generic 500 + problem+json,
    # and DOES NOT include the traceback in the body. The current
    # handler delegates to Starlette's default — which includes a full
    # Python traceback in the response. The test below asserts the
    # AC-10.3 invariant; if the body contains ``Traceback`` the test
    # fails (RED state).
    from taskq_api import service as _service_module

    def _raise_value_error(*args, **kwargs):
        raise ValueError(
            "boom: /Users/leaked/path/file.py crashed; "
            "SELECT * FROM api_keys; -- schema=public.tasks(id,name)"
        )

    # Force the list endpoint's repository to raise — every read
    # route (``GET /v1/tasks``) eventually calls into ``service.tasks``
    # or ``repository.task_repo``.
    monkeypatch.setattr(
        _service_module.tasks, "list_tasks", _raise_value_error,
    )

    response = _run(asgi_client.get("/v1/tasks", headers=auth_write))

    result_status = response.status_code
    body_text = response.text

    # A 500 path is the canonical scenario, but the test must accept a
    # broader range so a handler that emits e.g. a 503 (also a server
    # error) still passes the AC-10.3 invariant. The point is that the
    # body itself does NOT leak the stack trace, not that the status
    # code is exactly 500.
    assert result_status >= 500, (
        f"FR-10 AC-10.3 precondition violated: forced unhandled "
        f"{injected_exception_class} should yield a 5xx response, got "
        f"{result_status}; body={body_text!r}"
    )

    actual_hits = body_text.count(forbidden_pattern)

    # FR10-AC-10.3-no-traceback — applies_to (4): the body MUST NOT
    # contain the forbidden_pattern literal ("Traceback"). Trigger on
    # case-4's forbidden_pattern literal.
    if forbidden_pattern == "Traceback":
        assert forbidden_pattern == "Traceback"
        # The TEST_SPEC sub-assertion is `expected_hits == "0"`; assert
        # the count is zero so a single Traceback line fails the test
        # while a totally absent body also passes.
        assert actual_hits == int(expected_hits), (
            f"FR-10 AC-10.3 violated: 500 body MUST NOT contain "
            f"{forbidden_pattern!r} (AC-10.3 'detail 不得洩漏內部細節'); "
            f"got {actual_hits} hit(s); body={body_text!r}"
        )

    # FR10-AC-10.3-no-traceback predicate — expected_hits == "0"
    if expected_hits == "0":
        assert expected_hits == "0"
        assert actual_hits == 0, (
            f"FR-10 AC-10.3 violated: expected_hits={expected_hits!r} "
            f"but body contains {actual_hits} occurrence(s) of "
            f"{forbidden_pattern!r}; body={body_text!r}"
        )

    # Belt-and-braces — the absolute filesystem path that the exception
    # message embedded must also NOT appear in the wire body. AC-10.3
    # enumerates '檔案路徑' as a forbidden detail category; this
    # assertion pins that contract independently of the Traceback
    # literal.
    assert "/Users/leaked/path/file.py" not in body_text, (
        f"FR-10 AC-10.3 violated: 500 body MUST NOT contain an absolute "
        f"filesystem path (AC-10.3 '不得含 ... 檔案路徑'); "
        f"body={body_text!r}"
    )

    # FR-10 AC-10.3 — the response body MUST NOT leak SQL 陳述 or
    # 資料庫結構描述 (SPEC.md line 166). The injected exception
    # embeds both a SELECT statement and a ``schema=public.tasks``
    # column reference; the wire body must redact every forbidden
    # substring.
    assert "SELECT * FROM api_keys" not in body_text, (
        f"FR-10 AC-10.3 violated: 500 body MUST NOT leak SQL 陳述 "
        f"(SPEC.md line 166); body={body_text!r}"
    )
    assert "public.tasks" not in body_text, (
        f"FR-10 AC-10.3 violated: 500 body MUST NOT leak 資料庫結構描述 "
        f"(SPEC.md line 166); body={body_text!r}"
    )

    # AC-10.3 wire-shape guard — a sanitized 500 response MUST be the
    # canonical problem+json shape (type=/errors/internal, status=500,
    # detail=<generic message>, no internal leakage). This catches a
    # handler that emits an empty body (Starlette's default 500) — the
    # empty body has NO type field, so the assertion below fails RED.
    # GREEN TODO: ``taskq_api.app.create_app`` MUST register an
    # ``@application.exception_handler(Exception)`` that catches
    # unhandled exceptions and renders them as problem+json with a
    # sanitized detail field (no Traceback / no path / no SQL).
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"FR-10 AC-10.3 violated: unhandled-exception response must be "
        f"problem+json, got content-type={content_type!r}; "
        f"body={body_text!r}"
    )
    body = response.json()
    assert body.get("status", 0) >= 500, (
        f"FR-10 AC-10.3 violated: unhandled-exception problem+json must "
        f"carry a 5xx status, got body={body!r}"
    )
    assert body.get("type") == "/errors/internal", (
        f"FR-10 AC-10.3 violated: 500 problem+json must declare "
        f"type='/errors/internal', got {body.get('type')!r}; "
        f"body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 5: `test_correlation_id_in_header_and_log`
# TEST_SPEC.md FR-10 #5 — observability: every response carries an
# ``X-Correlation-Id`` header whose value is ALSO recorded in the
# server log so an operator can grep logs to trace the request. The
# log record name from TEST_SPEC is ``"audit"``.
# ---------------------------------------------------------------------------

# NFR-03 (error_handling: traceable errors), NFR-04 (sensitive data redaction), NFR-06 (architecture_constraints)
def test_correlation_id_in_header_and_log(asgi_client, auth_write, caplog):
    """FR-10 AC-10.4 — the correlation_id emitted on the response
    ``X-Correlation-Id`` header MUST also be recorded in the server log
    under the ``audit`` logger so an operator can grep logs to stitch a
    request across the full request lifecycle.

    Spec scenario: hit any /v1/* endpoint; read the response's
    ``X-Correlation-Id`` header and confirm a matching log record was
    emitted on the ``audit`` logger. The TEST_SPEC log_record_name is
    ``"audit"`` and ``expected_match`` is ``"true"`` — both are pinned
    as top-level if-triggers.

    NFR annotations:
      - NFR-03 (error_handling): traceable errors are the foundation of
        operator debugging — without a header-and-log pair, a 500
        leaves the on-call engineer blind.
      - NFR-06 (architecture layering): the correlation_id is generated
        in ``taskq_api.errors.new_correlation_id`` and surfaced through
        the composition-root exception handler; the audit logger is a
        sibling surface in the same independence module.
    """
    header_name = "X-Correlation-Id"  # case-5 input
    log_record_name = "audit"         # case-5 input
    expected_match = "true"           # case-5 input

    # Drive the request through the ASGI stack and capture the log
    # output emitted by the audit logger.
    with caplog.at_level(logging.INFO, logger=log_record_name):
        response = _run(asgi_client.get("/v1/tasks", headers=auth_write))

    # --- Response-side: header MUST be present -----------------------
    header_value = response.headers.get(header_name)

    # FR10-AC-10.4-corr-header-name — applies_to (5): the response
    # carries the canonical ``X-Correlation-Id`` header. Trigger on
    # case-5's header_name literal "X-Correlation-Id".
    if header_name == "X-Correlation-Id":
        assert header_name == "X-Correlation-Id"
        assert header_value is not None, (
            f"FR-10 AC-10.4 violated: response is missing the "
            f"{header_name!r} header; headers={dict(response.headers)!r}"
        )
        # The header value is a UUID4 hex (32 hex chars). Pinned via
        # the canonical regex so a handler that emits e.g. an empty
        # string or an arbitrary opaque token fails the contract.
        assert re.fullmatch(r"[0-9a-f]{32}", header_value), (
            f"FR-10 AC-10.4 violated: {header_name!r} value must be a "
            f"UUID4 hex (32 hex chars), got {header_value!r}"
        )

    # --- Log-side: same correlation_id MUST appear in the audit log --
    # The audit logger is the canonical surface; its records MUST
    # include the correlation_id emitted on the wire. We collect every
    # record on the ``audit`` logger and look for the header value in
    # each record's message.
    audit_records = [
        r for r in caplog.records if r.name == log_record_name
    ]

    # FR10-AC-10.4-corr-log-match — applies_to (5): the correlation_id
    # appears in the audit log. Trigger on case-5's expected_match
    # literal "true".
    if expected_match == "true":
        assert expected_match == "true"
        # GREEN TODO: ``taskq_api.app`` MUST log every request with the
        # audit logger and the correlation_id embedded in the message
        # so caplog can find it. The current request path emits
        # uvicorn's access log, NOT an application-level audit record.
        matched = any(
            header_value in (r.getMessage() if header_value else "")
            for r in audit_records
        )
        assert matched, (
            f"FR-10 AC-10.4 violated: correlation_id from "
            f"{header_name!r}={header_value!r} MUST also appear in "
            f"the {log_record_name!r} log; captured records="
            f"{[r.getMessage() for r in audit_records]!r}"
        )


# ---------------------------------------------------------------------------
# Coverage-completion unit tests for the FR-10 module bindings.
#
# The five TEST_SPEC.md cases above pin the acceptance-criteria contract;
# the tests below exercise the remaining branches of the FR-10 modules
# (``taskq_api.errors`` + ``taskq_api.app``) so every reachable line of
# the FR-10 surface is executed.
# ---------------------------------------------------------------------------


# NFR-06 (architecture_constraints: independence module surface)
def test_problem_base_class_exposes_required_fields():
    """FR-10 AC-10.2 — the ``Problem`` base class MUST expose the four
    required RFC-7807 fields (``type``, ``title``, ``status``,
    ``detail``) plus the optional ``instance`` and ``correlation_id``."""
    p = Problem(
        type_="/errors/test",
        title="Test",
        status=500,
        detail="detail-text",
        instance="/v1/test",
        correlation_id="abcdef1234567890abcdef1234567890",
    )

    assert p.type == "/errors/test"
    assert p.title == "Test"
    assert p.status == 500
    assert p.detail == "detail-text"
    assert p.instance == "/v1/test"
    assert p.correlation_id == "abcdef1234567890abcdef1234567890"


# NFR-06 (architecture_constraints: independence module surface)
def test_problem_body_serializes_all_wire_fields():
    """FR-10 AC-10.2 — ``taskq_api.errors.problem_body`` MUST emit
    ``type``, ``title``, ``status``, ``detail``, ``instance`` AND
    ``correlation_id`` on the wire so every response carries the full
    canonical AC-10.2 shape."""
    p = Problem(
        type_="/errors/test",
        title="Test",
        status=422,
        detail="d",
        instance="/v1/x",
        correlation_id="abcdef1234567890abcdef1234567890",
    )

    body = problem_body(p)

    # FR-10 AC-10.2 requires all six fields, INCLUDING ``correlation_id``
    # (SPEC.md line 165 — "body 欄位:type(title)、status、detail、instance、
    # correlation_id"). An implementation whose problem_body drops
    # correlation_id from the JSON violates the spec even though the
    # attribute still exists on the Problem object.
    for required_field in (
        "type", "title", "status", "detail", "instance", "correlation_id",
    ):
        assert required_field in body, (
            f"FR-10 AC-10.2 violated: problem_body is missing "
            f"{required_field!r}; body={body!r}"
        )
        assert body[required_field] == getattr(p, required_field), (
            f"FR-10 AC-10.2 violated: problem_body[{required_field!r}] "
            f"must mirror the Problem attribute; got {body[required_field]!r} "
            f"vs {getattr(p, required_field)!r}; body={body!r}"
        )


# NFR-09 (test_assertion_quality: correlation id type pinned)
def test_problem_correlation_id_defaults_to_uuid4_hex():
    """FR-10 AC-10.4 — when a Problem is constructed without an explicit
    ``correlation_id``, the constructor generates a fresh UUID4 hex."""
    p = Problem(
        type_="/errors/x", title="X", status=500, detail="d",
    )

    assert p.correlation_id is not None
    assert re.fullmatch(r"[0-9a-f]{32}", p.correlation_id), (
        f"FR-10 AC-10.4 violated: default correlation_id must be UUID4 "
        f"hex (32 chars), got {p.correlation_id!r}"
    )


# NFR-09 (test_assertion_quality: error-code map per AC-10.5)
def test_concrete_problem_subclasses_match_ac_10_5_error_code_map():
    """FR-10 AC-10.5 — the EIGHT error codes enumerated by SPEC.md line
    168 (422 validation / 401 unauthenticated / 403 forbidden / 404
    not-found / 409 conflict / 429 rate-limited / 503 not-ready /
    500 other) MUST each have a concrete ``Problem`` subclass (or
    base-class instantiation) with the matching ``status`` attribute.

    The 503 "not-ready" case is wired through the ``/readyz`` probe
    when the deployment forgot to run migrations (FR-09 AC-9.4 fail-
    closed contract); FR-10 AC-10.5 still demands the same
    problem+json wire shape be available for that status code.
    """
    assert ValidationProblem().status == 422
    assert _Unauthenticated().status == 401
    assert _Forbidden().status == 403
    assert NotFoundProblem().status == 404
    assert _Conflict().status == 409
    assert RateLimitedProblem().status == 429
    # 503 — SPEC.md line 168 "503 未就緒"; the implementation MUST
    # support rendering the not-ready condition as a problem+json with
    # status == 503. We construct a Problem directly so the assertion
    # does not depend on a specific ``ServiceUnavailableProblem`` class
    # name (the spec enumerates the status code, not the subclass
    # identifier) — this pins the invariant across whichever name the
    # GREEN implementation chooses.
    assert Problem(
        type_="/errors/service-unavailable", title="Service Unavailable",
        status=503, detail="not ready",
    ).status == 503
    # The 500 "other" case is the base Problem at status=500.
    assert Problem(
        type_="/errors/internal", title="Internal", status=500, detail="x",
    ).status == 500


# Helpers — local imports so the import block above stays flat and the
# constructor of each subclass is exercised through its default
# detail. The actual imports live at module scope (above) for the
# NotFoundProblem / ValidationProblem / RateLimitedProblem classes;
# the UnauthenticatedProblem and ForbiddenProblem classes are also
# imported through ``taskq_api.errors`` via the wildcard-style
# coverage tests below.
def _Unauthenticated():
    from taskq_api.errors import UnauthenticatedProblem
    return UnauthenticatedProblem()


def _Forbidden():
    from taskq_api.errors import ForbiddenProblem
    return ForbiddenProblem()


def _Conflict():
    from taskq_api.errors import ConflictProblem
    return ConflictProblem()


# NFR-04 (sensitive data redaction), NFR-06 (architecture_constraints)
def test_redact_secrets_replaces_full_match():
    """NFR-04 — ``taskq_api.errors.redact_secrets`` replaces every match
    of the secret regex (sk-/token=/Bearer/postgres) with ``[REDACTED]``.
    AC-10.3 derives from the same "no internal detail leak" invariant:
    the redaction surface is upstream of the problem+json wire shape."""
    from taskq_api.errors import redact_secrets

    # ``sk-`` keys (NFR-04.1 secret class) — match is the entire string.
    assert redact_secrets("sk-abcdef1234567890") == "[REDACTED]"
    # ``token=`` query-string secrets — match is the entire string.
    assert redact_secrets("token=secretvalue") == "[REDACTED]"
    # ``Bearer`` headers — the ``Bearer mytoken`` substring is replaced;
    # surrounding prefix text is preserved (the regex matches the
    # secret token, not the whole line).
    assert redact_secrets("Authorization: Bearer mytoken") == \
        "Authorization: [REDACTED]"
    # ``postgres://`` connection strings (NFR-04.2) — match is the
    # entire string.
    assert redact_secrets("postgres://user:pass@host/db") == "[REDACTED]"
    # Plain text with no secret must pass through unchanged.
    assert redact_secrets("plain text without secrets") == \
        "plain text without secrets"


# NFR-04 (sensitive data redaction: empty / None input handled)
def test_redact_secrets_handles_empty_input():
    """NFR-04 — the redaction surface MUST NOT crash on empty / falsy
    input; the upstream caller (``taskq_api.app`` exception handler)
    feeds it raw ``str(exc)`` text that may be empty for some
    exception classes."""
    from taskq_api.errors import redact_secrets

    assert redact_secrets("") == ""
    assert redact_secrets("a sk-abcdef1234567890 b") == "a [REDACTED] b"


# NFR-06 (architecture_constraints: composition root mounts handler)
def test_app_registers_problem_exception_handler():
    """FR-10 AC-10.1 — ``taskq_api.app.create_app`` MUST register an
    ``exception_handler(Problem, ...)`` so every concrete subclass
    surfaces as ``application/problem+json`` on the wire."""
    # FastAPI stores exception handlers in ``app.exception_handlers``.
    handlers = app.exception_handlers

    # The Problem base class is registered as a handler key (handlers
    # are keyed by exception class). Walking MRO is unnecessary because
    # FastAPI looks up handlers by ``type(exc)``.
    assert Problem in handlers, (
        f"FR-10 AC-10.1 violated: taskq_api.app.create_app did not "
        f"register an exception_handler for Problem; "
        f"registered={list(handlers.keys())!r}"
    )
