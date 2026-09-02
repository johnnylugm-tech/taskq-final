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
from taskq_api.service import tasks as service_tasks
from taskq_api.service.auth import Principal

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50

# Scope hierarchy: `read < write < admin`. Each handler asserts the
# required scope inline so the per-route constant is visible at the
# declaration site (FR-04 AC-4.3 keeps auth/scope split).
_READ_OR_HIGHER = ("read", "write", "admin")
_WRITE_OR_HIGHER = ("write", "admin")
_ADMIN_ONLY = ("admin",)


def _assert_scope(principal: Principal, allowed: tuple[str, ...]) -> None:
    """[FR-04] Raise ``ForbiddenProblem`` unless ``principal.scope`` is in
    ``allowed``. Centralises the per-route scope guard so every handler
    shares one raise path."""
    if principal.scope not in allowed:
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
    _assert_scope(principal, _WRITE_OR_HIGHER)
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
    _assert_scope(principal, _READ_OR_HIGHER)
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
    _assert_scope(principal, _READ_OR_HIGHER)
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
    _assert_scope(principal, _ADMIN_ONLY)
    service_tasks.delete_task(task_id)
    return JSONResponse(status_code=204, content=None)


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
    _assert_scope(principal, _READ_OR_HIGHER)
    runs = service_tasks.list_runs(task_id)
    return {"items": [_run_to_out(r) for r in runs]}
