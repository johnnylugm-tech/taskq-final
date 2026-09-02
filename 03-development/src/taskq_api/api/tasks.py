"""[FR-01, FR-02] `/v1/tasks*` router.

Thin handlers (≤40 lines, NFR-11) that delegate every non-trivial step to
``taskq_api.service.tasks``. Each handler carries a docstring citing the
FR / NFR it implements and the OpenAPI ``summary`` / ``description`` that
NFR-05 asserts against ``/openapi.json``.

Citations:
    - SPEC.md §3 FR-01 (CRUD), FR-02 (runs)
    - SPEC.md §3 FR-10 (problem+json error contract)
    - SAD.md §3.1, §2.8
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from taskq_api.api.deps import require_scope
from taskq_api.errors import ForbiddenProblem, ValidationProblem
from taskq_api.models.schemas import TaskCreate
from taskq_api.service import runner as service_runner
from taskq_api.service import tasks as service_tasks
from taskq_api.service.auth import Principal, verify_scope

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


def _require_scope(principal: Principal, required: str) -> None:
    """[FR-04 AC-4.1, AC-4.2] Raise ``ForbiddenProblem`` unless
    ``principal.scope`` is at least ``required`` in the strict
    ``read < write < admin`` order.

    Delegates to :func:`taskq_api.service.auth.verify_scope` so the per-
    route guard shares one raise path AND one hierarchy definition with
    every other ``/v1/*`` handler (FR-04 AC-4.3 — single chokepoint).
    """
    if not verify_scope(principal, required):
        raise ForbiddenProblem()


def _to_out(row: dict) -> dict:
    """[FR-01] Normalize a repository row to the ``TaskOut`` wire shape."""
    return {
        "id": row["id"],
        "name": row["name"],
        "command": row["command"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


def _run_to_out(row: dict) -> dict:
    """[FR-02] Normalize a repository run row to the ``RunOut`` wire shape."""
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "exit_code": row["exit_code"],
        "stdout_tail": row["stdout_tail"],
        "stderr_tail": row["stderr_tail"],
        "duration_ms": row["duration_ms"],
        "finished_at": row["finished_at"].isoformat(),
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    description=(
        "[FR-01 AC-1.1] Validates the body with the `TaskCreate` pydantic "
        "model and creates one task. Requires scope=write."
    ),
)
async def create_task(
    payload: TaskCreate,
    request: Request,
    principal: Principal = Depends(require_scope),
) -> JSONResponse:
    """[FR-01 AC-1.1] `POST /v1/tasks` (scope=write)."""
    _require_scope(principal, "write")
    row = service_tasks.create_task(payload)
    return JSONResponse(status_code=201, content={"id": row["id"]})


@router.get(
    "/{task_id}",
    summary="Get one task",
    description=(
        "[FR-01 AC-1.3] Returns the task's full row; 404 + problem+json "
        "for unknown ids. Requires scope=read."
    ),
)
async def get_task(
    task_id: str,
    principal: Principal = Depends(require_scope),
) -> dict:
    """[FR-01 AC-1.3] `GET /v1/tasks/{id}` (scope=read)."""
    _require_scope(principal, "read")
    row = service_tasks.get_task(task_id)
    return _to_out(row)


@router.get(
    "",
    summary="List tasks (cursor pagination)",
    description=(
        "[FR-01 AC-1.4] Cursor-based pagination (no offset). Defaults: "
        "limit=50, max=200; >200 returns 422 + problem+json. Requires "
        "scope=read."
    ),
)
async def list_tasks(
    limit: Optional[int] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    principal: Principal = Depends(require_scope),
) -> dict:
    """[FR-01 AC-1.4] `GET /v1/tasks` (scope=read) — cursor pagination."""
    _require_scope(principal, "read")
    applied = _DEFAULT_LIMIT if limit is None else int(limit)
    if applied < 1:
        raise ValidationProblem("limit must be >= 1")
    if applied > _MAX_LIMIT:
        raise ValidationProblem(f"limit must be <= {_MAX_LIMIT}")
    items, next_cursor, _applied = service_tasks.list_tasks(
        limit=applied, cursor=cursor, status=status_filter,
    )
    return {
        "items": [_to_out(it) for it in items],
        "next_cursor": next_cursor,
        "limit": applied,
    }


@router.delete(
    "/{task_id}",
    summary="Delete a task",
    description=(
        "[FR-01 AC-1.5] Deletes the task and cascades to its "
        "`task_results` rows in a single transaction. Requires scope=admin."
    ),
)
async def delete_task(
    task_id: str,
    principal: Principal = Depends(require_scope),
) -> JSONResponse:
    """[FR-01 AC-1.5] `DELETE /v1/tasks/{id}` (scope=admin)."""
    _require_scope(principal, "admin")
    service_tasks.delete_task(task_id)
    return JSONResponse(status_code=204, content=None)


@router.post(
    "/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a task",
    description=(
        "[FR-02 AC-2.1] Accepts the task for execution and returns 202 with "
        "a `run_id`; a task already in the `running` state is rejected with "
        "409 + problem+json. Requires scope=write."
    ),
)
async def run_task(
    task_id: str,
    principal: Principal = Depends(require_scope),
) -> JSONResponse:
    """[FR-02 AC-2.1, AC-2.3] `POST /v1/tasks/{id}/run` (scope=write).

    Answers ``202 Accepted`` with the ``run_id`` the caller polls for via
    `GET /v1/tasks/{id}/runs`. The command that executes is the task's own
    registered ``command`` — the request body never becomes the argv source.

    Citations: SPEC.md line 95 (202 + run_id), line 97 (state machine).
    """
    _require_scope(principal, "write")
    run_id = service_runner.start_run(task_id)
    return JSONResponse(status_code=202, content={"run_id": run_id})


@router.get(
    "/{task_id}/runs",
    summary="List run history (newest first)",
    description=(
        "[FR-02 AC-2.5] Returns `task_results` for the task, newest first. "
        "Requires scope=read."
    ),
)
async def list_runs(
    task_id: str,
    principal: Principal = Depends(require_scope),
) -> dict:
    """[FR-02 AC-2.5] `GET /v1/tasks/{id}/runs` (scope=read)."""
    _require_scope(principal, "read")
    runs = service_tasks.list_runs(task_id)
    return {"items": [_run_to_out(r) for r in runs]}
