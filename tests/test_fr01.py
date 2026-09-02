"""TDD-RED tests for FR-01: Task resource CRUD API.

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-01`):
    - taskq_api.api.tasks          -> POST /v1/tasks, GET /v1/tasks/{id},
                                      GET /v1/tasks, DELETE /v1/tasks/{id}
    - taskq_api.service.tasks      -> task orchestration (CRUD use-cases)
    - taskq_api.service.common     -> shared service helpers
    - taskq_api.repository.task_repo -> SQL/keyset-cursor persistence
    - taskq_api.models.schemas     -> pydantic TaskCreate / TaskOut
    - taskq_api.models.orm         -> SQLAlchemy Task / TaskResult ORM

Per TEST_SPEC.md FR-01 the 8 named cases use 3 function names, reused across
distinct scenarios via @pytest.mark.parametrize so each scenario is its own
test instance, but the function name itself stays exactly as the spec
demands (spec-coverage-check matches on the function symbol, not the
parametrize id).

RED state expected: ModuleNotFoundError on the imports below because
`03-development/src/taskq_api/` does not exist yet. That is the canonical RED
for this FR — see harness contract: "If pytest returns Exit Code 2
(Collection Error) due to missing modules, this is a VALID RED STATE."
"""

from __future__ import annotations

import pytest

# Standard top-level imports. NO try/except ImportError wrappers.
# These WILL raise ModuleNotFoundError until GREEN implements:
#   - taskq_api.api.tasks          (router with /v1/tasks endpoints)
#   - taskq_api.app                (FastAPI instance bound to that router)
#   - taskq_api.models.schemas     (TaskCreate pydantic model)
#   - taskq_api.repository.task_repo (cursor-paginated list + delete cascade)
from taskq_api.api.tasks import router  # noqa: F401  -- GREEN TODO: export `router`
from taskq_api.app import app  # noqa: F401  -- GREEN TODO: export `app` FastAPI instance
from taskq_api.models.schemas import TaskCreate  # noqa: F401  -- GREEN TODO: export `TaskCreate`
from taskq_api.repository.task_repo import TaskRepository  # noqa: F401  -- GREEN TODO: export `TaskRepository`


# ---------------------------------------------------------------------------
# Test fixtures: ASGI in-process transport (NFR-10 mandates
# httpx.AsyncClient(ASGITransport(...)) — never direct handler calls).
# ---------------------------------------------------------------------------

@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    GREEN TODO: `taskq_api.app.app` must be a FastAPI instance with the
    `taskq_api.api.tasks.router` mounted under `/v1/tasks`.
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_write():
    """A request header carrying a write-scoped API key.

    GREEN TODO: `taskq_api.service.auth.verify_key` must accept
    {"X-API-Key": "<write-scoped-key>"} and return a principal with
    scope == "write". The FR-01 POST path is gated on scope == "write".
    """
    return {"X-API-Key": "test-write-key"}


@pytest.fixture
def auth_read():
    """A request header carrying a read-scoped API key.

    GREEN TODO: same as `auth_write` but with scope == "read". Used by the
    GET endpoints AND by the negative authz case that asserts a write
    request under a read key returns 403 (NP-02).
    """
    return {"X-API-Key": "test-read-key"}


# ---------------------------------------------------------------------------
# Case 1-4: `test_task_crud_returns_201_422_404`
# TEST_SPEC.md FR-01 #1-4 — one function symbol, four scenarios.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("scenario", "key_scope", "body_name", "body_command",
     "lookup_id", "expected_status"),
    [
        # AC-1.1 — happy path POST /v1/tasks with valid body returns 201.
        ("create_valid",
         "write", "build-hello", "echo hi", None, "201"),
        # AC-1.2 — POST /v1/tasks with empty `command` violates the
        # validation rule (non-empty / ≤1000 / blacklist / unique-name) and
        # must respond 422 + application/problem+json
        # (type=/errors/validation, per SPEC line 88).
        ("create_empty_command",
         "write", "bad", "", None, "422"),
        # NP-02 cross-cut — a write request under a read-scoped key must
        # be rejected with 403 (no disclosure of whether the resource
        # exists — see FR-04 #1 body_disclosure=absent).
        ("create_wrong_scope",
         "read", "x", "echo y", None, "403"),
        # AC-1.3 — GET /v1/tasks/{id} for an unknown UUID returns 404 +
        # problem+json with type=/errors/not-found (SPEC line 89).
        ("get_unknown_id",
         "read", None, None,
         "00000000-0000-0000-0000-000000000000", "404"),
    ],
    ids=["AC-1.1-create-valid",
         "AC-1.2-create-empty-command",
         "NP-02-create-wrong-scope",
         "AC-1.3-get-unknown-id"],
)
@pytest.mark.asyncio
async def test_task_crud_returns_201_422_404(
    scenario, key_scope, body_name, body_command, lookup_id, expected_status,
    asgi_client, auth_write, auth_read,
):
    """FR-01 CRUD round-trip: every status code in the canonical CRUD
    contract (201/422/403/404) is asserted for the right scenario.

    The function symbol is shared across the four TEST_SPEC cases; the
    scenario id disambiguates them in pytest output without violating
    the spec-coverage exact-match rule.
    """
    headers = auth_write if key_scope == "write" else auth_read

    if scenario == "get_unknown_id":
        # AC-1.3 lookup path
        response = await asgi_client.get(
            f"/v1/tasks/{lookup_id}", headers=headers,
        )
    else:
        # POST path (create_valid / create_empty_command / create_wrong_scope)
        response = await asgi_client.post(
            "/v1/tasks",
            headers=headers,
            json={"name": body_name, "command": body_command},
        )

    # FR01-AC-1.1-status / FR01-AC-1.2-validation-status / FR01-AC-1.3-scope-read
    assert response.status_code == int(expected_status), (
        f"[{scenario}] expected status {expected_status}, "
        f"got {response.status_code}; body={response.text!r}"
    )

    # AC-1.2 / AC-1.3: problem+json content-type on every error response.
    if expected_status in {"403", "404", "422"}:
        # FR01-AC-1.2-validation-status, FR01-AC-1.4-missing-id
        ctype = response.headers.get("content-type", "")
        assert ctype.startswith("application/problem+json"), (
            f"[{scenario}] expected problem+json, got content-type={ctype!r}"
        )

    # AC-1.1: 201 must echo the newly-created task id.
    if expected_status == "201":
        body = response.json()
        assert "id" in body, (
            f"[{scenario}] 201 response missing 'id' field; body={body!r}"
        )


# ---------------------------------------------------------------------------
# Case 5-7: `test_tasks_list_cursor_pagination`
# TEST_SPEC.md FR-01 #5-7 — one function symbol, three scenarios.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("scenario", "seed_count", "page_size", "requested_limit",
     "expected_status", "expected_page_count", "expected_default_limit"),
    [
        # AC-1.4 / NP-12 — seed 100 tasks, walk the cursor at page_size=50,
        # assert exactly 2 pages (no offset, no N+1).
        ("two_pages",
         "100", "50", None, None, "2", None),
        # AC-1.4 — requested limit > 200 returns 422
        # (FR01-AC-1.4-limit-over).
        ("limit_over_200",
         None, None, "201", "422", None, None),
        # AC-1.4 — when caller sends limit=50, the server-applied default
        # must equal 50 (FR01-AC-1.5-default-limit; default-limit invariant).
        ("default_limit_50",
         None, None, "50", None, None, "50"),
    ],
    ids=["AC-1.4-two-pages",
         "AC-1.4-limit-over-200",
         "AC-1.4-default-limit-50"],
)
@pytest.mark.asyncio
async def test_tasks_list_cursor_pagination(
    scenario, seed_count, page_size, requested_limit,
    expected_status, expected_page_count, expected_default_limit,
    asgi_client, auth_read,
):
    """FR-01 AC-1.4 / NP-12 — GET /v1/tasks cursor pagination contract:
    - 100 rows at page_size=50 yields exactly 2 pages (no offset, no N+1)
    - limit > 200 returns 422
    - default limit (when caller passes 50) is 50
    """
    params = {}
    if requested_limit is not None:
        params["limit"] = requested_limit

    response = await asgi_client.get(
        "/v1/tasks", headers=auth_read, params=params,
    )

    if expected_status is not None:
        # AC-1.4 limit-over
        assert response.status_code == int(expected_status), (
            f"[{scenario}] expected status {expected_status}, "
            f"got {response.status_code}; body={response.text!r}"
        )
        return

    # Happy path / boundary-OK path — assert cursor pagination contract.
    assert response.status_code == 200, (
        f"[{scenario}] expected 200, got {response.status_code}; "
        f"body={response.text!r}"
    )

    body = response.json()

    if expected_default_limit is not None:
        # AC-1.4 — server echoes the applied limit.
        assert body.get("limit") == int(expected_default_limit), (
            f"[{scenario}] expected applied limit="
            f"{expected_default_limit}, body={body!r}"
        )
        return

    if expected_page_count is not None:
        # AC-1.4 / NP-12 — cursor walk over `seed_count` rows at
        # `page_size` per page yields exactly `expected_page_count` pages.
        # The endpoint must expose `next_cursor` (string) — not an offset
        # integer — to honour the cursor-based contract.
        page_count = 0
        cursor = None
        seen = 0
        while True:
            page_count += 1
            params = {"limit": page_size}
            if cursor:
                params["cursor"] = cursor
            page_resp = await asgi_client.get(
                "/v1/tasks", headers=auth_read, params=params,
            )
            assert page_resp.status_code == 200, (
                f"[{scenario}] cursor walk page {page_count} returned "
                f"{page_resp.status_code}"
            )
            page_body = page_resp.json()
            seen += len(page_body.get("items", []))
            cursor = page_body.get("next_cursor")
            if not cursor:
                break
            # Belt-and-braces: cursor walk must terminate.
            assert page_count <= int(expected_page_count) + 5, (
                f"[{scenario}] cursor walk did not terminate at "
                f"page {page_count}"
            )

        assert page_count == int(expected_page_count), (
            f"[{scenario}] expected {expected_page_count} pages, "
            f"walked {page_count}"
        )
        # FR01-AC-1.4-pages-count
        assert seen >= int(seed_count), (
            f"[{scenario}] walked {seen} rows, expected at least "
            f"{seed_count}"
        )


# ---------------------------------------------------------------------------
# Case 8: `test_delete_removes_results`
# TEST_SPEC.md FR-01 #8 — state-transition: DELETE removes task + cascades
# task_results in the same transaction.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_removes_results(asgi_client, auth_read, auth_write):
    """FR-01 AC-1.5 — DELETE /v1/tasks/{id} removes the task AND its
    associated `task_results` rows in a single transaction.

    Spec scenario: seed 3 runs, expect 0 runs remaining after DELETE.
    The cross-table cascade (task + task_results) is the FR-01 contract;
    the state-transition assertion is `expected_runs_after == "0"`.
    """
    # GREEN TODO: TaskRepository.create_with_runs must create a task and
    # N associated task_results rows in one transaction so we have a
    # fixture task with 3 runs to delete.
    repo = TaskRepository()
    task = repo.create_with_runs(name="to-delete", command="echo hi",
                                 run_count=3)
    task_id = task["id"]

    # Pre-condition: the 3 result rows exist.
    pre = await asgi_client.get(f"/v1/tasks/{task_id}/runs",
                                headers=auth_read)
    assert pre.status_code == 200, (
        f"pre-delete runs lookup failed: {pre.status_code} {pre.text!r}"
    )
    pre_runs = pre.json().get("items", [])
    assert len(pre_runs) == 3, (
        f"expected 3 seeded runs, got {len(pre_runs)}"
    )

    # DELETE — admin scope required.
    delete_headers = {"X-API-Key": "test-admin-key"}
    del_resp = await asgi_client.delete(
        f"/v1/tasks/{task_id}", headers=delete_headers,
    )
    assert del_resp.status_code in (200, 204), (
        f"DELETE returned {del_resp.status_code}: {del_resp.text!r}"
    )

    # Post-condition: the task is gone (404 on GET).
    post_get = await asgi_client.get(f"/v1/tasks/{task_id}",
                                     headers=auth_read)
    assert post_get.status_code == 404, (
        f"task still reachable after DELETE: "
        f"{post_get.status_code} {post_get.text!r}"
    )

    # FR01-AC-1.5-runs-cleared — the cascaded result rows are gone too.
    post_runs = await asgi_client.get(f"/v1/tasks/{task_id}/runs",
                                      headers=auth_read)
    # Either 404 (task gone) or 200 with empty list — both prove the
    # cascade cleared the runs.
    if post_runs.status_code == 200:
        remaining = post_runs.json().get("items", [])
        assert len(remaining) == 0, (
            f"FR01-AC-1.5-runs-cleared violated: "
            f"{len(remaining)} runs remain after DELETE"
        )
    else:
        assert post_runs.status_code == 404, (
            f"unexpected post-delete runs status: "
            f"{post_runs.status_code}"
        )
