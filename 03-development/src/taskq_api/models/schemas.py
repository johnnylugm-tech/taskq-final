"""[FR-01] Pydantic v2 request / response schemas — L1.
# pragma: no error-handling  # pure data/constants — no I/O to handle

Defines the wire shape of the Task resource and the validation rules used by
AC-1.1 / AC-1.2 (non-empty, ≤1000 chars). Blacklist + uniqueness live in the
service layer (per SAD.md §2.5 logical constraints).

Citations:
    - SPEC.md §3 FR-01 (CRUD endpoints + validation)
    - SPEC.md §3 FR-10 (problem+json error contract)
    - SAD.md §2.5
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# Maximum command / name length, per SPEC.md FR-01 AC-1.2 ("≤1000").
_MAX_COMMAND_LEN = 1000
_MAX_NAME_LEN = 255


class TaskCreate(BaseModel):
    """[FR-01] Body shape for `POST /v1/tasks`.

    Citations: SPEC.md §3 FR-01 AC-1.1, AC-1.2; SAD.md §3.1.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_NAME_LEN,
        description="Unique task name.",
    )
    command: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_COMMAND_LEN,
        description=(
            "Shell command line. Validation: non-empty, ≤1000 chars, "
            "injection-character blacklist (enforced in service layer)."
        ),
    )


class TaskOut(BaseModel):
    """[FR-01] Single-task response (matches `GET /v1/tasks/{id}` body)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    command: str
    status: str
    created_at: datetime


class TaskListPage(BaseModel):
    """[FR-01] Cursor-paginated list response for `GET /v1/tasks`.

    Citations: SPEC.md §3 FR-01 AC-1.4 (cursor-based pagination).
    """

    model_config = ConfigDict(extra="forbid")

    items: list[TaskOut]
    next_cursor: Optional[str] = None
    limit: int


class ProblemOut(BaseModel):
    """[FR-10] RFC 7807 problem+json body."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    instance: Optional[str] = None


class RunOut(BaseModel):
    """[FR-02] Single run record (v3 schema)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    finished_at: datetime
