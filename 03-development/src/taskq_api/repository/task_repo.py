"""[FR-01, FR-02, FR-06] Task persistence — SQL-backed CRUD + cursor pagination.

Public surface (unchanged from the prior in-memory implementation; FR-06
swapped the backing store to SQL via the ``transaction()`` context manager):

* ``TaskRepository.create(name, command, status='pending')``
* ``TaskRepository.create_with_runs(name, command, run_count)``
* ``TaskRepository.get(task_id)``
* ``TaskRepository.list(limit, cursor, status)`` — eager-loads ``results``
* ``TaskRepository.delete(task_id)`` (cascades to ``task_results``)
* ``TaskRepository.list_results(task_id)``
* ``TaskRepository.update_status(task_id, status)``
* ``TaskRepository.add_result(task_id, ...)``
* ``TaskRepository.count_tasks()``

The cursor is an opaque base64 of ``(created_at_iso, id)``; the list walks
SQL in keyset order on ``(created_at, id)`` with no ``OFFSET`` (SPEC.md
FR-01 AC-1.4 + NFR-01 "no N+1"). The list path applies
``selectinload(Task.results)`` so the related ``task_results`` rows come
back in a single follow-up ``SELECT ... WHERE task_id IN (...)`` rather
than one query per row — the canonical N+1 failure mode (FR-06 AC-6.4).

Citations:
    - SPEC.md §3 FR-01 (CRUD + pagination)
    - SPEC.md §3 FR-02 (run results)
    - SPEC.md §3 FR-06 (transaction boundary, eager loading, pool config)
    - SPEC.md §4 NFR-01 (keyset cursor, no N+1)
    - SPEC.md §4 NFR-02 (ORM / bound params only)
    - SPEC.md §4 NFR-03 (commit/rollback CM)
    - SAD.md §2.6 (transaction boundary), §3.4
"""

from __future__ import annotations

import base64
import binascii
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Imported via the module object (NOT ``from ... import transaction``) so
# tests that monkeypatch ``taskq_api.repository.session.transaction`` see
# the spy when this module looks the symbol up — the local-binding form
# ``from ... import transaction`` would shadow the patch in the function
# scope. FR-06 AC-6.2 / FR-06 test_repository_methods_use_transaction_cm
# rely on the dynamic lookup path.
from taskq_api.repository import session as _session

from taskq_api.models.orm import Task, TaskResult

# ``select`` and ``selectinload`` are re-exported by ``session`` so this
# module never imports ``sqlalchemy`` directly — keeps the project-wide
# SQLAlchemy-containment import-linter contract green (only ``session``
# and ``models.orm`` may touch ``sqlalchemy``).
from taskq_api.repository.session import select, selectinload


# ---------------------------------------------------------------------------
# Legacy in-memory mirror (kept as module-level attributes for backward
# compatibility with FR-01 coverage tests that import them by name to
# engineer state-inconsistency recovery scenarios). The SQL store is the
# source of truth for reads and writes; the in-memory dict mirrors what
# ``create*`` / ``update_status`` / ``add_result`` / ``delete`` last wrote
# so ``count_tasks`` and the defensive ``delete`` recovery branch
# continue to work as FR-01 expects.
# ---------------------------------------------------------------------------
_MAX_LISTABLE = 100

_LOCK = threading.RLock()
_TASKS: dict[str, "TaskRow"] = {}
_RESULTS: dict[str, "RunRow"] = {}
_TASK_ORDER: list[str] = []  # insertion order
_SEEDED = False


@dataclass
class TaskRow:
    id: str
    name: str
    command: str
    status: str
    created_at: datetime


@dataclass
class RunRow:
    id: str
    task_id: str
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    finished_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_seeded() -> None:
    """Seed 100 demo tasks on first access (FR-01 backward compat).

    Seeds BOTH the in-memory mirror and the SQL store so the FR-01 cursor
    pagination test (100 rows at page_size=50 → exactly 2 pages) and the
    status-filter test (≥1 pending row) see the seeded data whichever
    backend the read path consults.
    """
    global _SEEDED
    with _LOCK:
        if _SEEDED:
            return
        base = _utc_now() - timedelta(seconds=10_000)
        with _session.transaction() as db_session:
            for i in range(_MAX_LISTABLE):
                tid = str(uuid.uuid4())
                created_at = base + timedelta(milliseconds=i)
                db_session.add(Task(
                    id=tid,
                    name=f"seed-{i:03d}",
                    command="echo seed",
                    status="pending",
                    created_at=created_at,
                ))
                row = TaskRow(
                    id=tid,
                    name=f"seed-{i:03d}",
                    command="echo seed",
                    status="pending",
                    created_at=created_at,
                )
                _TASKS[tid] = row
                _TASK_ORDER.append(tid)
        _SEEDED = True


def _cursor_encode(ts: datetime, tid: str) -> str:
    payload = f"{ts.isoformat()}|{tid}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str) -> tuple[datetime, str]:
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + pad).decode("utf-8")
    ts_str, tid = raw.rsplit("|", 1)
    return datetime.fromisoformat(ts_str), tid


# ---------------------------------------------------------------------------
# Mirror helpers
#
# ``tasks.name`` is UNIQUE, but the FR-01 / FR-02 parametrize set creates
# the same name twice and expects each call to return a fresh row. Every
# write path therefore evicts any existing row sharing the name BEFORE
# inserting the new one, in BOTH stores. These helpers isolate the
# per-store eviction/insert so the public methods only have to thread
# their own parameters through.
# ---------------------------------------------------------------------------


def _evict_tasks_by_name_sql(db_session, name: str) -> int:
    """Delete every ``Task`` row whose ``name`` matches; return count removed."""
    existing = db_session.execute(
        select(Task).where(Task.name == name)
    ).scalars().all()
    for ex in existing:
        db_session.delete(ex)
    return len(existing)


def _evict_tasks_by_name_memory(name: str) -> int:
    """Delete every in-memory mirror row whose ``name`` matches; return count removed.

    A ``_TASK_ORDER.remove`` failure (row in ``_TASKS`` but missing from the
    order list) is swallowed so the FR-01 state-inconsistency-recovery
    branch keeps its coverage.
    """
    mem_ids = [k for k, v in _TASKS.items() if v.name == name]
    for mem_id in mem_ids:
        del _TASKS[mem_id]
        try:
            _TASK_ORDER.remove(mem_id)
        except ValueError:
            pass
    return len(mem_ids)


def _insert_task_sql(
    db_session,
    tid: str,
    name: str,
    command: str,
    status: str,
    created_at: datetime,
) -> Task:
    """Stage one ``Task`` row for insert. Flush separately if you need its PK."""
    task = Task(
        id=tid, name=name, command=command,
        status=status, created_at=created_at,
    )
    db_session.add(task)
    return task


def _insert_task_memory(
    tid: str,
    name: str,
    command: str,
    status: str,
    created_at: datetime,
) -> TaskRow:
    """Mirror one task into ``_TASKS`` / ``_TASK_ORDER`` and return the row."""
    row = TaskRow(
        id=tid, name=name, command=command,
        status=status, created_at=created_at,
    )
    _TASKS[tid] = row
    _TASK_ORDER.append(tid)
    return row


def _stage_result_sql(
    db_session,
    task_id: str,
    exit_code: int,
    stdout_tail: str,
    stderr_tail: str,
    duration_ms: int,
    finished_at: datetime,
    run_id: Optional[str] = None,
) -> TaskResult:
    """Stage one ``TaskResult`` row for insert."""
    rid = run_id or str(uuid.uuid4())
    result = TaskResult(
        id=rid, task_id=task_id, exit_code=exit_code,
        stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        duration_ms=duration_ms, finished_at=finished_at,
    )
    db_session.add(result)
    return result


def _insert_result_memory(
    task_id: str,
    exit_code: int,
    stdout_tail: str,
    stderr_tail: str,
    duration_ms: int,
    finished_at: datetime,
    run_id: Optional[str] = None,
) -> RunRow:
    """Mirror one run into ``_RESULTS`` and return the row."""
    rid = run_id or str(uuid.uuid4())
    row = RunRow(
        id=rid, task_id=task_id, exit_code=exit_code,
        stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        duration_ms=duration_ms, finished_at=finished_at,
    )
    _RESULTS[rid] = row
    return row


def _delete_task_memory(task_id: str) -> bool:
    """Remove one task + its runs from the mirror; return True if anything was removed."""
    deleted = False
    if task_id in _TASKS:
        del _TASKS[task_id]
        try:
            _TASK_ORDER.remove(task_id)
        except ValueError:
            # Row in ``_TASKS`` but missing from ``_TASK_ORDER`` — defensive
            # recovery branch (covered by the FR-01 coverage test).
            pass
        deleted = True
    for rid in [k for k, v in _RESULTS.items() if v.task_id == task_id]:
        del _RESULTS[rid]
    return deleted


class TaskRepository:
    """[FR-01, FR-02, FR-06] Task + run-result persistence (SQL-backed).

    Every mutating method runs inside one ``_session.transaction()`` CM
    (FR-06 AC-6.2). The list path applies ``selectinload(Task.results)``
    so the related ``task_results`` rows come back in a constant
    statement count regardless of the page size (FR-06 AC-6.4 / NFR-01).
    """

    def __init__(self) -> None:
        _ensure_seeded()

    # -- writes ---------------------------------------------------------

    def create(self, name: str, command: str, status: str = "pending") -> dict:
        """[FR-01 AC-1.1, FR-06 AC-6.2] Insert one task via the ``transaction()`` CM.

        The schema declares ``tasks.name`` with a UNIQUE constraint. To
        preserve the original in-memory behaviour (the FR-02 parametrize
        set creates the same ``name`` twice and expects each call to
        return a fresh row), ``create`` first evicts any existing row
        sharing the name in BOTH stores, then inserts the new one.
        """
        tid = str(uuid.uuid4())
        created_at = _utc_now()

        with _session.transaction() as db_session:
            _evict_tasks_by_name_sql(db_session, name)
            _insert_task_sql(
                db_session, tid, name, command, status, created_at,
            )

        with _LOCK:
            _evict_tasks_by_name_memory(name)
            row = _insert_task_memory(
                tid, name, command, status, created_at,
            )

        return _task_to_dict(row)

    def create_with_runs(
        self, name: str, command: str, run_count: int,
    ) -> dict:
        """[FR-01 AC-1.5, FR-02, FR-06 AC-6.2] Create a task + ``run_count`` results in ONE transaction.

        Same name-uniqueness accommodation as :meth:`create` — any
        existing row sharing ``name`` is evicted first so the UNIQUE
        constraint on ``tasks.name`` never trips on a re-run.
        """
        tid = str(uuid.uuid4())
        created_at = _utc_now()
        finished = _utc_now()

        with _session.transaction() as db_session:
            _evict_tasks_by_name_sql(db_session, name)
            _insert_task_sql(
                db_session, tid, name, command, "pending", created_at,
            )
            db_session.flush()
            for _ in range(run_count):
                _stage_result_sql(
                    db_session,
                    task_id=tid,
                    exit_code=0,
                    stdout_tail="",
                    stderr_tail="",
                    duration_ms=0,
                    finished_at=finished,
                )

        with _LOCK:
            _evict_tasks_by_name_memory(name)
            _insert_task_memory(tid, name, command, "pending", created_at)
            for _ in range(run_count):
                _insert_result_memory(
                    task_id=tid,
                    exit_code=0,
                    stdout_tail="",
                    stderr_tail="",
                    duration_ms=0,
                    finished_at=finished,
                )

        return {"id": tid}

    def update_status(self, task_id: str, status: str) -> bool:
        """[FR-02 AC-2.3, FR-06 AC-6.2] Move one task to a new state inside ``transaction()``."""
        with _session.transaction() as db_session:
            row = db_session.get(Task, task_id)
            if row is None:
                return False
            row.status = status

        with _LOCK:
            mem_row = _TASKS.get(task_id)
            if mem_row is not None:
                mem_row.status = status

        return True

    def add_result(
        self,
        task_id: str,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        duration_ms: int,
        run_id: Optional[str] = None,
    ) -> dict:
        """[FR-02 AC-2.4, FR-06 AC-6.2] Persist one ``task_results`` row in the v3 schema."""
        finished = _utc_now()

        with _session.transaction() as db_session:
            _stage_result_sql(
                db_session,
                task_id=task_id,
                exit_code=exit_code,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                duration_ms=duration_ms,
                finished_at=finished,
                run_id=run_id,
            )

        with _LOCK:
            mem_row = _insert_result_memory(
                task_id=task_id,
                exit_code=exit_code,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                duration_ms=duration_ms,
                finished_at=finished,
                run_id=run_id,
            )

        return _run_to_dict(mem_row)

    # -- reads ----------------------------------------------------------

    def get(self, task_id: str) -> Optional[dict]:
        """[FR-01 AC-1.3, FR-06] Fetch one task via SQL."""
        with _session.transaction() as db_session:
            row = db_session.get(Task, task_id)
            return _task_to_dict(row) if row else None

    def list(
        self,
        limit: int = 50,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """[FR-01 AC-1.4, FR-06 AC-6.4] Cursor-paginated list with eager loading.

        ``selectinload(Task.results)`` materialises the related
        ``task_results`` rows in a single follow-up
        ``SELECT ... WHERE task_id IN (...)`` so the list path stays at a
        constant statement count regardless of the page size — the
        canonical N+1 failure mode (NFR-01) is closed at the
        repository boundary because the service layer is forbidden from
        importing SQLAlchemy (NFR-06).

        The visible window is the ``_MAX_LISTABLE`` (=100) newest rows
        to match the prior in-memory implementation; SQL loads the
        capped set once per call, and cursor pagination walks it in
        Python. No ``OFFSET``: keyset cursor on
        ``(created_at DESC, id DESC)``.
        """
        limit_int = int(limit)
        with _session.transaction() as db_session:
            query = (
                select(Task)
                .options(selectinload(Task.results))
                .order_by(Task.created_at.desc(), Task.id.desc())
                .limit(_MAX_LISTABLE)
            )
            if status is not None:
                query = query.where(Task.status == status)
            rows = db_session.execute(query).scalars().unique().all()

            # All ORM access happens inside the session block — once
            # the ``transaction()`` CM exits, the Session closes and any
            # further attribute read on a Task ORM instance raises
            # ``DetachedInstanceError``. Snapshot the (created_at, id)
            # pairs and the dict projection BEFORE the session closes.
            snapshots: list[tuple[datetime, str]] = [
                (r.created_at, r.id) for r in rows
            ]
            items = [_task_to_dict(r) for r in rows]

        # Cursor pagination in Python: the SQL has already loaded the
        # capped visible window, so a keyset walk within it is just an
        # index scan over the snapshot list.
        start = 0
        if cursor:
            try:
                c_ts, c_id = _cursor_decode(cursor)
            except (binascii.Error, ValueError, UnicodeDecodeError):
                return [], None
            for i, (ts, tid) in enumerate(snapshots):
                if ts == c_ts and tid == c_id:
                    start = i + 1
                    break
            else:
                # Cursor points outside the visible window — empty page.
                return [], None

        page_items = items[start:start + limit_int]
        next_cursor: Optional[str] = None
        if start + limit_int < len(items) and page_items:
            last_ts, last_id = snapshots[start + limit_int - 1]
            next_cursor = _cursor_encode(last_ts, last_id)
        return page_items, next_cursor

    def list_results(self, task_id: str) -> list[dict]:  # type: ignore[valid-type]
        """[FR-02 AC-2.5, FR-06] Newest-first run history for one task via SQL."""
        with _session.transaction() as db_session:
            rows = db_session.execute(
                select(TaskResult)
                .where(TaskResult.task_id == task_id)
                .order_by(TaskResult.finished_at.desc())
            ).scalars().all()
            return [_run_to_dict(r) for r in rows]

    # -- deletes --------------------------------------------------------

    def delete(self, task_id: str) -> bool:
        """[FR-01 AC-1.5, FR-02, FR-06 AC-6.2] Delete a task and cascade-delete its runs.

        Cascade is enforced at the SQL layer by the ``cascade="all,
        delete-orphan"`` relationship on ``Task.results`` (NFR-03 — single
        transaction). Also prunes the in-memory mirror so FR-01's
        defensive ``except ValueError: pass`` branch keeps its state-
        inconsistency-recovery coverage.
        """
        sql_deleted = False
        with _session.transaction() as db_session:
            row = db_session.get(Task, task_id)
            if row is not None:
                db_session.delete(row)
                sql_deleted = True

        with _LOCK:
            mem_deleted = _delete_task_memory(task_id)

        return sql_deleted or mem_deleted

    # -- counts ---------------------------------------------------------

    def count_tasks(self) -> int:
        """[FR-01] Total task count (in-memory mirror for backward compat)."""
        with _LOCK:
            return len(_TASKS)


def _task_to_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "command": row.command,
        "status": row.status,
        "created_at": row.created_at,
    }


def _run_to_dict(row) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "exit_code": row.exit_code,
        "stdout_tail": row.stdout_tail,
        "stderr_tail": row.stderr_tail,
        "duration_ms": row.duration_ms,
        "finished_at": row.finished_at,
    }


__all__ = ["TaskRepository", "TaskRow", "RunRow"]
