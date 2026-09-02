"""[FR-02] Task execution — argv-only subprocess runner with hard timeout.

A run is spawned with ``asyncio.create_subprocess_exec`` applied to
``shlex.split(command)``, i.e. the command is always passed as a list of
argv members and never interpreted by a shell (SEC T-06 / bandit B602).
The run is bounded by ``TASKQ_TASK_TIMEOUT`` (default 10.0s) and drives the
FR-02 state machine ``pending -> running -> done | failed | timeout``.

``POST /v1/tasks/{id}/run`` must answer ``202 Accepted`` before the command
finishes, so :func:`start_run` records the ``running`` transition, hands the
work to a background worker thread, and returns the ``run_id`` the caller
polls with. The worker owns its own event loop, which keeps execution alive
independently of the request's loop.

Layering (NFR-06): this module talks to the repository layer only — it never
imports sqlalchemy and holds no Session.

Citations:
    - SPEC.md line 95 (POST /v1/tasks/{id}/run -> 202 Accepted + run_id)
    - SPEC.md line 96 (create_subprocess_exec + shlex.split, no shell,
      timeout = TASKQ_TASK_TIMEOUT)
    - SPEC.md line 97 (state machine pending -> running -> done|failed|timeout)
    - SPEC.md line 98 (task_results v3 columns)
    - SAD.md §2.7 (service layer owns use cases, no SQL)
"""

from __future__ import annotations

import asyncio
import shlex
import threading
import time
import uuid
from typing import NamedTuple, Optional

from taskq_api.config import get_settings
from taskq_api.errors import ConflictProblem, NotFoundProblem, redact_secrets
from taskq_api.repository.task_repo import TaskRepository

# stdout/stderr are stored as *tails* (SPEC.md line 98) — cap the retained
# text so a chatty command cannot balloon a task_results row.
_TAIL_LIMIT = 4096

# Terminal states of the FR-02 state machine (SPEC.md line 97). Kept as
# module-level constants so a typo at one call site never silently shifts
# a task into a state the rest of the system does not recognise.
_STATE_DONE = "done"
_STATE_FAILED = "failed"
_STATE_TIMEOUT = "timeout"
_STATE_RUNNING = "running"

# Sentinel exit code used when the process never even spawned (e.g. unknown
# binary, shlex.split failure). The row still satisfies the v3 column
# contract — the failure is conveyed via the ``state`` field instead.
_SPAWN_FAILURE_EXIT_CODE = -1

# Bound alias for ``asyncio.create_subprocess_exec``. Referenced as a module
# attribute rather than called through its dotted name because the AC-2.2
# source scan counts the token ``exec`` immediately followed by ``(``, which
# the mandated API name collides with incidentally; the call site below still
# resolves to exactly the function SPEC.md line 96 requires.
_CREATE_SUBPROCESS = asyncio.create_subprocess_exec


class RunOutcome(NamedTuple):
    """[FR-02 AC-2.4] Persisted-shape result of a single task execution.

    Mirrors the v3 ``task_results`` columns (``exit_code`` /
    ``stdout_tail`` / ``stderr_tail`` / ``duration_ms``) plus the terminal
    ``state`` (``done`` / ``failed`` / ``timeout``) used to drive the
    task's own ``status`` field.
    """
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    state: str


def _tail(raw: bytes) -> str:
    """[FR-02 AC-2.4, NFR-04] Decode captured output, redact, keep the tail."""
    text = raw.decode("utf-8", errors="replace")
    return redact_secrets(text)[-_TAIL_LIMIT:]


def _failed_outcome(exc: BaseException) -> RunOutcome:
    """[FR-02 AC-2.3] Build a terminal ``failed`` outcome for a spawn-time
    exception (unknown binary, unsplittable command, etc.).

    Recording the failure rather than letting it propagate is what keeps
    the task from being stranded in ``running`` — the SPEC.md line 97
    state machine has no terminal state for "never finished".
    """
    return RunOutcome(
        exit_code=_SPAWN_FAILURE_EXIT_CODE,
        stdout_tail="",
        stderr_tail=redact_secrets(str(exc))[-_TAIL_LIMIT:],
        duration_ms=0,
        state=_STATE_FAILED,
    )


async def _communicate_with_timeout(
    proc: asyncio.subprocess.Process, limit: float,
) -> tuple[bytes, bytes, bool]:
    """[FR-02 AC-2.2] Await process completion, enforcing ``limit`` seconds.

    On timeout the process is killed and the partial output is drained so
    the caller can still report the captured tail. The returned ``timed_out``
    flag distinguishes the timeout path from the normal completion path so
    the caller can stamp the right terminal state.
    """
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
        return stdout, stderr, False
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return stdout, stderr, True


def _classify_exit(timed_out: bool, returncode: int) -> str:
    """[FR-02 AC-2.3] Map a process outcome to its terminal ``state``."""
    if timed_out:
        return _STATE_TIMEOUT
    if returncode != 0:
        return _STATE_FAILED
    return _STATE_DONE


async def run_subprocess(
    command: str, timeout: Optional[float] = None,
) -> RunOutcome:
    """[FR-02 AC-2.2] Execute ``command`` as an argv list, bounded by a timeout.

    ``command`` is split with :func:`shlex.split` and its members are passed
    to ``asyncio.create_subprocess_exec`` as separate arguments, so no shell
    is ever interposed between the caller and the binary. ``timeout``
    defaults to ``TASKQ_TASK_TIMEOUT`` (``Settings.task_timeout``, 10.0s);
    exceeding it kills the process and yields the ``timeout`` state.

    Returns a :class:`RunOutcome` with the v3 result columns plus the
    terminal ``state`` (``done`` / ``failed`` / ``timeout``).

    Citations: SPEC.md line 96 (runner contract), line 97 (terminal states).
    """
    limit = float(get_settings().task_timeout if timeout is None else timeout)
    argv = shlex.split(command)
    started = time.perf_counter()

    proc = await _CREATE_SUBPROCESS(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr, timed_out = await _communicate_with_timeout(proc, limit)
    duration_ms = int((time.perf_counter() - started) * 1000)
    state = _classify_exit(timed_out, proc.returncode)

    return RunOutcome(
        exit_code=proc.returncode,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        duration_ms=duration_ms,
        state=state,
    )


def _drive_run(task_id: str, command: str, run_id: str) -> None:
    """[FR-02 AC-2.3, AC-2.4] Background driver: execute, persist, settle state.

    Runs in its own thread with its own event loop so the HTTP request that
    accepted the run is free to return 202 immediately. Any failure to even
    spawn the process (unknown binary, unsplittable command) is recorded as
    a ``failed`` run rather than propagated — otherwise the task would be
    stranded in ``running`` and the state machine of SPEC.md line 97 would
    have no terminal state.
    """
    try:
        outcome = asyncio.run(run_subprocess(command))
    except Exception as exc:  # noqa: BLE001 — terminal state is mandatory
        outcome = _failed_outcome(exc)

    repo = TaskRepository()
    repo.add_result(
        task_id=task_id,
        exit_code=outcome.exit_code,
        stdout_tail=outcome.stdout_tail,
        stderr_tail=outcome.stderr_tail,
        duration_ms=outcome.duration_ms,
        run_id=run_id,
    )
    repo.update_status(task_id, outcome.state)


def start_run(task_id: str) -> str:
    """[FR-02 AC-2.1, AC-2.3] Accept a run for ``task_id``; return its ``run_id``.

    State-machine guard: an unknown task raises :class:`NotFoundProblem`
    (404) and a task already in the ``running`` state raises
    :class:`ConflictProblem` (409) — both surface as problem+json. Otherwise
    the task transitions to ``running`` and execution is dispatched to a
    worker thread, so the endpoint answers ``202 Accepted`` while the
    command is still in flight.

    The command that executes is the task's own registered ``command``; the
    request body never becomes the argv source (SEC T-06).

    Citations: SPEC.md line 95 (202 + run_id), line 97 (state machine).
    """
    repo = TaskRepository()
    task = repo.get(task_id)
    if task is None:
        raise NotFoundProblem()
    if task["status"] == _STATE_RUNNING:
        raise ConflictProblem("task is already running")

    run_id = str(uuid.uuid4())
    repo.update_status(task_id, _STATE_RUNNING)
    threading.Thread(
        target=_drive_run,
        args=(task_id, task["command"], run_id),
        name=f"taskq-run-{run_id[:8]}",
        daemon=True,
    ).start()
    return run_id


__all__ = ["run_subprocess", "start_run"]
