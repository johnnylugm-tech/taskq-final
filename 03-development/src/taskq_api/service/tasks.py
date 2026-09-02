"""[FR-01, FR-02] Use-case orchestration for the Task resource.

The HTTP handler is a thin wrapper around these functions; everything below
runs without an HTTP context.

Citations:
    - SPEC.md §3 FR-01 (CRUD use cases)
    - SPEC.md §3 FR-02 (run history)
    - SAD.md §2.7
"""

from __future__ import annotations

from typing import Optional

from taskq_api.errors import ConflictProblem, NotFoundProblem
from taskq_api.models.schemas import TaskCreate
from taskq_api.repository.task_repo import TaskRepository


def create_task(payload: TaskCreate) -> dict:
    """[FR-01 AC-1.1] Persist a new task; returns ``{"id": ...}``."""
    repo = TaskRepository()
    return repo.create(name=payload.name, command=payload.command)


def get_task(task_id: str) -> dict:
    """[FR-01 AC-1.3] Fetch one task; raise ``NotFoundProblem`` on miss."""
    repo = TaskRepository()
    row = repo.get(task_id)
    if row is None:
        raise NotFoundProblem()
    return row


def list_tasks(
    limit: int = 50,
    cursor: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], Optional[str], int]:
    """[FR-01 AC-1.4] Cursor-paginated list.

    Returns ``(items, next_cursor, applied_limit)`` — the applied limit is
    echoed back so the response body can advertise the effective page size.
    """
    repo = TaskRepository()
    items, next_cursor = repo.list(limit=limit, cursor=cursor, status=status)
    return items, next_cursor, limit


def delete_task(task_id: str) -> bool:
    """[FR-01 AC-1.5] Delete a task; raise ``NotFoundProblem`` on miss.

    The cascade-delete to ``task_results`` is enforced inside the repository
    (NFR-03 — single transaction).
    """
    repo = TaskRepository()
    ok = repo.delete(task_id)
    if not ok:
        raise NotFoundProblem()
    return True


def list_runs(task_id: str) -> list[dict]:
    """[FR-02 AC-2.5] Newest-first run history."""
    repo = TaskRepository()
    if repo.get(task_id) is None:
        raise NotFoundProblem()
    return repo.list_results(task_id)
