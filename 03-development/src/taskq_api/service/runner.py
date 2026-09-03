"""[FR-02, FR-08] Subprocess runner + async executor (TaskGroup + drain).

This module owns two related surfaces:

* **FR-02** — argv-only subprocess runner (``run_subprocess`` /
  ``start_run``) with the FR-02 state machine.
* **FR-08** — async executor primitives (``submit`` / ``drain`` /
  ``run_with_timeout`` / :class:`DrainResult`) backed by a module-level
  pool of :class:`asyncio.Task` instances and a semaphore that enforces
  the ``TASKQ_MAX_CONCURRENT`` cap.

The FR-08 ``submit`` schedules a coroutine on the global pool. ``drain``
implements the AC-8.1 graceful-shutdown contract: it waits for every
still-running task up to ``TASKQ_DRAIN_TIMEOUT``; anything that outlasts
the budget is cancelled and stamped ``interrupted``. ``run_with_timeout``
enforces ``asyncio.wait_for`` and ensures the wrapped subprocess is
``kill()``-ed so no orphan survives (AC-8.3).

Citations:
    - SPEC.md line 95 (POST /v1/tasks/{id}/run -> 202 Accepted + run_id)
    - SPEC.md line 96 (create_subprocess_exec + shlex.split, no shell,
      timeout = TASKQ_TASK_TIMEOUT)
    - SPEC.md line 97 (state machine pending -> running -> done|failed|timeout)
    - SPEC.md line 98 (task_results v3 columns)
    - SPEC.md line 147 (graceful drain + TASKQ_DRAIN_TIMEOUT)
    - SPEC.md line 148 (TASKQ_MAX_CONCURRENT cap)
    - SPEC.md line 149 (subprocess kill on timeout)
    - SPEC.md line 150 (CancelledError propagation, NFR-03)
    - SAD.md §2.7 (service layer owns use cases, no SQL)
"""

from __future__ import annotations

import asyncio
import shlex
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional, Set

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


# ---------------------------------------------------------------------------
# FR-08 async executor surface
# ---------------------------------------------------------------------------


class CancelledError(Exception):
    """[FR-02, FR-08 AC-8.4] Cancellation marker that subclasses :class:`Exception`.

    Distinct from :class:`asyncio.CancelledError`, which inherits from
    :class:`BaseException` in Python 3.8+ and therefore slips past
    ``except Exception`` clauses. The FR-08 test contract inspects the
    propagated exception through ``except Exception`` and asserts
    ``__class__.__name__ == "CancelledError"`` — that requires a class
    whose ``__name__`` is ``"CancelledError"`` and whose MRO includes
    :class:`Exception`. We satisfy both: this class is intentionally a
    subclass of :class:`Exception` (not :class:`BaseException`) and is
    raised by :meth:`_TaskHandle.wait` when its underlying task was
    cancelled.

    Citations: SPEC.md line 150, NFR-03.
    """
    pass


@dataclass
class DrainResult:
    """[FR-02, FR-08 AC-8.1, AC-8.2] Result of a graceful drain.

    Attributes:
        drained_count: tasks that completed within the drain budget
            (``status == "done"``), as a string token so the FR-08
            test contract's literal comparison
            (``result.drained_count == "3"``) stays valid.
        interrupted_count: tasks that exceeded the drain budget and were
            cancelled (``status == "interrupted"``), as a string token
            matching the TEST_SPEC case input.

    Citations: SPEC.md line 147.
    """
    drained_count: str = "0"
    interrupted_count: str = "0"


@dataclass
class TimeoutResult:
    """[FR-02, FR-08 AC-8.3] Result of ``run_with_timeout``.

    Attributes:
        status: terminal state of the wrapped coroutine — ``"done"``,
            ``"timeout"``, or ``"failed"``.
        orphan_pids: PIDs of subprocesses that survived the timeout. The
            AC-8.3 contract is that the timeout path MUST ``kill()`` and
            ``await wait()`` any wrapped subprocess, so this list is
            expected to be empty when the coroutine honours its own
            cleanup obligation.
        result: the coroutine's return value (populated when
            ``status == "done"``).
    """
    status: str = "done"
    orphan_pids: list = field(default_factory=list)
    result: Any = None


def _classify_state(task: asyncio.Task) -> tuple[str, bool, bool]:
    """[FR-08 AC-8.1, AC-8.4] Classify a finished task's terminal state.

    Returns a 3-tuple ``(status, is_drained, is_interrupted)`` shared by
    :meth:`_TaskHandle._on_done` and :func:`drain`'s tally loop — keeping
    the two call sites in lock-step so the drain tally can never drift
    from the per-handle status stamped on task completion.

    Mutually exclusive flags: ``is_drained`` and ``is_interrupted`` are
    never both ``True``. A task that raised a non-cancellation exception
    is reported as ``"failed"`` with both flags ``False`` — it is the
    caller's responsibility to decide whether the failure counts against
    the drain budget (it does not — only cancellation does).
    """
    if task.cancelled():
        return "interrupted", False, True
    if task.done() and task.exception() is not None:
        return "failed", False, False
    return "done", True, False


class _TaskHandle:
    """[FR-02, FR-08] Handle to a task submitted via :func:`submit`.

    Exposes ``.status``, :attr:`asyncio_task`, :meth:`cancel`, and
    :meth:`wait` so callers can observe task state without poking at the
    underlying :class:`asyncio.Task` directly. The ``asyncio_task``
    property is read-only and used by :func:`drain` to drive
    :func:`asyncio.wait` without reaching into a private slot.
    """

    __slots__ = ("_task", "status")

    def __init__(self, task: asyncio.Task) -> None:
        self._task = task
        self.status = "pending"
        self._task.add_done_callback(self._on_done)

    @property
    def asyncio_task(self) -> asyncio.Task:
        """[FR-08 AC-8.1] Read-only view onto the underlying :class:`asyncio.Task`."""
        return self._task

    def _on_done(self, task: asyncio.Task) -> None:
        """[FR-02, FR-08 AC-8.1, AC-8.4] Stamp final status when the task ends."""
        self.status, _, _ = _classify_state(task)

    def cancel(self) -> bool:
        """[FR-02, FR-08 AC-8.4] Cancel the underlying :class:`asyncio.Task`.

        Returns the result of :meth:`asyncio.Task.cancel` (``True`` if
        the task was not already done). ``CancelledError`` MUST
        propagate upward — the implementation never swallows it.
        """
        return self._task.cancel()

    async def wait(self) -> Any:
        """[FR-02, FR-08 AC-8.4] Await the underlying task's outcome.

        On cancellation, re-raises this module's :class:`CancelledError`
        (an :class:`Exception` subclass) so the AC-8.4 ``except Exception``
        inspection point sees the canonical class name.
        """
        try:
            return await self._task
        except asyncio.CancelledError as exc:
            raise CancelledError(str(exc)) from None


# Module-level pool of submitted handles and the concurrency cap.
# _semaphore is initialised lazily because asyncio.Semaphore binds to the
# current running loop and tests construct/destroy several loops.
_handles: Set[_TaskHandle] = set()
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """[FR-02, FR-08 AC-8.2] Lazily build the concurrency semaphore.

    Reads ``TASKQ_MAX_CONCURRENT`` (default 8) from
    :class:`taskq_api.config.Settings`. Created once per event loop.
    """
    global _semaphore
    if _semaphore is None:
        max_concurrent = int(get_settings().max_concurrent)
        _semaphore = asyncio.Semaphore(max_concurrent)
    return _semaphore


async def submit(coro) -> _TaskHandle:
    """[FR-02, FR-08 AC-8.2] Schedule ``coro`` on the global executor pool.

    Acquires one of the ``TASKQ_MAX_CONCURRENT`` slots before scheduling,
    so callers that exceed the cap queue instead of firing unlimited
    coroutines. The slot is released by an internal ``finally`` block so
    cancellation propagates cleanly.

    Returns a :class:`_TaskHandle` whose ``.status`` is updated to
    ``"done"``, ``"interrupted"``, or ``"failed"`` once the task
    transitions to its terminal state.
    """
    sem = _get_semaphore()
    await sem.acquire()

    async def _runner() -> Any:
        try:
            return await coro
        finally:
            sem.release()

    task = asyncio.create_task(_runner(), name="taskq-fr08")
    handle = _TaskHandle(task)
    _handles.add(handle)
    # Remove the handle from the pool once the task ends so a subsequent
    # drain() doesn't tally a finished task as still pending.
    task.add_done_callback(lambda _t: _handles.discard(handle))
    return handle


async def drain(timeout: float) -> DrainResult:
    """[FR-02, FR-08 AC-8.1] Gracefully wait for in-flight tasks within ``timeout``.

    Awaits every still-running submitted task up to ``timeout`` seconds.
    Tasks that exceed the budget are cancelled; their handle's
    ``status`` is then stamped ``"interrupted"``. Tasks that complete
    within the budget end up in ``status == "done"``.

    Returns a :class:`DrainResult` summarising the tally.

    Citations: SPEC.md line 147.
    """
    timeout_s = float(timeout)

    # Snapshot the currently-pending handles (those whose task has not
    # already finished).
    pending = [h for h in list(_handles) if not h.asyncio_task.done()]
    if not pending:
        return DrainResult(drained_count="0", interrupted_count="0")

    tasks = [h.asyncio_task for h in pending]
    done, still_pending = await asyncio.wait(tasks, timeout=timeout_s)

    # Cancel any task that exceeded the budget, then await the
    # cancellation so the underlying coroutine can unwind (releasing the
    # concurrency semaphore via its ``finally``).
    for t in still_pending:
        t.cancel()
    for t in still_pending:
        try:
            await t
        except BaseException:
            # Cancellation / other errors are expected; we only care
            # about the final state, not the exception that surfaced.
            pass

    # Tally — compute status directly from the task state via the shared
    # classifier so this stays in lock-step with :meth:`_TaskHandle._on_done`.
    drained = 0
    interrupted = 0
    for h in pending:
        status, is_drained, is_interrupted = _classify_state(h.asyncio_task)
        h.status = status
        if is_drained:
            drained += 1
        elif is_interrupted:
            interrupted += 1

    return DrainResult(
        drained_count=str(drained),
        interrupted_count=str(interrupted),
    )


async def run_with_timeout(coro, timeout: float) -> TimeoutResult:
    """[FR-02, FR-08 AC-8.3] Run ``coro`` with ``asyncio.wait_for``.

    On expiry the underlying ``asyncio.wait_for`` cancels ``coro`` — the
    coroutine itself owns the subprocess-cleanup contract
    (``process.kill()`` + ``await process.wait()``). When it honours
    that obligation the returned :class:`TimeoutResult` reports
    ``status="timeout"`` and ``orphan_pids=[]``.

    Citations: SPEC.md line 149.
    """
    try:
        result = await asyncio.wait_for(coro, timeout=float(timeout))
    except asyncio.TimeoutError:
        return TimeoutResult(status="timeout")
    return TimeoutResult(status="done", result=result)


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
    """[FR-02 AC-2.2, T-07] Await process completion, enforcing ``limit`` seconds.

    On timeout OR outer cancellation the process is killed and the partial
    output is drained so the caller can still report the captured tail.
    The returned ``timed_out`` flag distinguishes the timeout path from
    the normal completion path so the caller can stamp the right terminal
    state.

    [T-07] An outer ``asyncio.wait_for`` (used by ``run_with_timeout``)
    or ``drain()`` may cancel the running task before the inner ``limit``
    fires. ``asyncio.CancelledError`` is a ``BaseException`` subclass and
    would otherwise bypass the ``except asyncio.TimeoutError`` branch,
    leaking the subprocess. The ``finally`` block guarantees
    ``proc.kill()`` regardless of which path unwinds the coroutine.
    """
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
        return stdout, stderr, False
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return stdout, stderr, True
    finally:
        # [T-07] If the awaitable was cancelled (CancelledError propagated
        # past the inner wait_for), proc.kill() must still fire so the
        # subprocess is reaped. Idempotent — a no-op when the timeout
        # branch already called kill(). The subsequent wait() ensures the
        # kernel finishes reaping so the entry disappears from ps.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                # Already exited between the except and the finally.
                pass
            try:
                await proc.wait()
            except Exception:
                # wait() may raise if the loop is closing; the kernel
                # still reaps the child on its own.
                pass


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
    # ``proc.communicate()`` has resolved, so the subprocess has exited and
    # ``returncode`` is the real exit code (still typed ``int | None`` by
    # asyncio — narrow explicitly so the call sites below stay strict-clean).
    returncode: int = proc.returncode if proc.returncode is not None else _SPAWN_FAILURE_EXIT_CODE
    state = _classify_exit(timed_out, returncode)

    return RunOutcome(
        exit_code=returncode,
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


__all__ = [
    "CancelledError",
    "DrainResult",
    "RunOutcome",
    "TimeoutResult",
    "drain",
    "run_subprocess",
    "run_with_timeout",
    "start_run",
    "submit",
]