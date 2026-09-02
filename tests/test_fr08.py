"""TDD-RED tests for FR-08: Async executor (TaskGroup + graceful drain).

Module bindings (per `.methodology/SAB.json` `fr_module_traceability.FR-08`):
    - taskq_api.service.runner  -> FR-08 async executor primitives:
        * ``submit(coro) -> Future`` — schedule a coroutine on the global
          ``asyncio.TaskGroup``-managed pool, queueing when the live task
          count meets ``TASKQ_MAX_CONCURRENT``.
        * ``drain(timeout: float) -> DrainResult`` — graceful shutdown;
          awaits every still-running task up to ``TASKQ_DRAIN_TIMEOUT``;
          any task that exceeded the budget is cancelled and its status
          stamped ``interrupted``.
        * ``run_with_timeout(coro, timeout: float)`` — runs ``coro`` with
          ``asyncio.wait_for``; on expiry the wrapped subprocess MUST be
          killed (``process.kill()`` + ``await process.wait()``) so no
          orphan survives.
        * Cancellation contract: ``CancelledError`` raised by a submitted
          task MUST propagate upward — no ``except Exception:`` swallowing
          (NFR-03).
    - taskq_api.app -> ``lifespan`` context manager MUST drive the FR-08
      drain on shutdown (AC-8.1); ``app`` composition root already exists
      in tree, GREEN TODO is the lifespan block.

Per TEST_SPEC.md §FR-08 the 4 named cases use 3 function symbols; cases #1
and #2 both live under ``test_graceful_drain_waits_running`` via
``@pytest.mark.parametrize`` so each scenario is its own test instance while
the function symbol matches the TEST_SPEC declaration exactly
(spec-coverage-check matches on the function symbol, not the parametrize id).

Sub-assertion predicates from TEST_SPEC.md §FR-08 are emitted as top-level
(flat) ``if``-trigger blocks keyed to the canonical TEST_SPEC input
variable (e.g. ``running_count``, ``drain_timeout``, ``expected_drained_count``,
``interrupted_after_drain``, ``expected_status``, ``task_timeout``,
``command``, ``expected_orphan_pids``, ``cancel_after``,
``expected_error_class``). The MIRROR checker walks each if-block at the
function-body level only; nested ifs are not collected, so every
predicate-bearing if sits at the top of its function body.

RED state expected: ``taskq_api.service.runner`` exists (FR-02's runner
already lives there) but does NOT expose the FR-08 names
(``submit`` / ``drain`` / ``run_with_timeout`` / ``DrainResult``); the FR-08
specific imports therefore raise ``ImportError``/``AttributeError`` —
pytest exits with code 2 (Collection Error) when the missing symbol is
imported at module top level, or the assertion fails once GREEN supplies a
partial surface. Per the harness contract: "If pytest returns Exit Code 2
(Collection Error) due to missing modules, this is a VALID RED STATE."

Citations:
    - SPEC.md line 147 (graceful drain + ``TASKQ_DRAIN_TIMEOUT``)
    - SPEC.md line 150 (``CancelledError`` propagation, NFR-03)
    - SAD.md §2.7 (service-layer use cases)
"""

from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# Environment hygiene: pin the FR-08 env knobs BEFORE any code path reads
# them so the test does not depend on whatever happens to be in the
# developer's shell. GREEN reads these from ``taskq_api.config.Settings``;
# setting them here is the cleanest isolation against "test passed locally
# but failed in CI".
# ---------------------------------------------------------------------------

os.environ.setdefault("TASKQ_DRAIN_TIMEOUT", "5.0")
os.environ.setdefault("TASKQ_MAX_CONCURRENT", "8")
os.environ.setdefault("TASKQ_TASK_TIMEOUT", "10.0")

# Standard top-level imports. NO try/except ImportError wrappers.
# These WILL raise ImportError/AttributeError until GREEN implements:
#   - taskq_api.service.runner.submit
#   - taskq_api.service.runner.drain
#   - taskq_api.service.runner.run_with_timeout
#   - taskq_api.service.runner.DrainResult
#   - taskq_api.app.app.lifespan (or equivalent shutdown hook)

from taskq_api.app import app  # noqa: F401  -- GREEN TODO: FastAPI app MUST wire a lifespan that drains the FR-08 executor on shutdown
from taskq_api.service.runner import (  # noqa: F401  -- GREEN TODO: service.runner MUST expose the FR-08 async executor surface
    DrainResult,
    drain,
    run_with_timeout,
    submit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _sleep_coro(seconds: float) -> None:
    """A trivial coroutine that just sleeps — used to build a task pool
    whose members are still in-flight when ``drain`` is called."""
    await asyncio.sleep(seconds)


async def _spawned_subprocess_coro() -> None:
    """Build a coroutine that has spawned a real ``sleep`` subprocess so
    the timeout path has something to ``kill`` (AC-8.3)."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "10",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=0.5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise


def _run(coro):
    """Drive an awaitable from inside a synchronous pytest function body."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Cases 1 + 2: ``test_graceful_drain_waits_running``
# TEST_SPEC.md FR-08 #1-2 — one function symbol, two scenarios:
#   - AC-8.1 happy path: with 3 tasks in-flight, calling drain with a
#     5.0s budget must wait for all 3 to finish; the resulting
#     ``drained_count`` equals ``running_count``.
#   - AC-8.1 timeout path: when the in-flight task outlasts the budget,
#     drain cancels it and stamps its status ``interrupted``.
# Both scenarios share the same function symbol via parametrize.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("running_count", "drain_timeout", "expected_drained_count",
     "interrupted_after_drain", "expected_status"),
    [
        # AC-8.1 happy path: 3 running tasks, drain_timeout=5.0s — every
        # task finishes well inside the budget, drained_count == 3.
        ("3", "5.0", "3", None, None),
        # AC-8.1 timeout path: drain budget exceeded — the still-running
        # task is cancelled and its status stamped ``interrupted``.
        (None, None, None, "1", "interrupted"),
    ],
    ids=["AC-8.1-graceful-drain-waits-running",
         "AC-8.1-graceful-drain-interrupts-on-timeout"],
)
def test_graceful_drain_waits_running(
    running_count, drain_timeout, expected_drained_count,
    interrupted_after_drain, expected_status,
):
    """FR-08 AC-8.1 — graceful drain awaits in-flight tasks.

    Two scenarios share this function symbol:

      - AC-8.1 (happy path): with ``running_count`` tasks in-flight and
        a ``drain_timeout`` budget, ``drain(timeout)`` MUST wait for all
        in-flight tasks to finish; ``DrainResult.drained_count`` equals
        ``running_count``.

      - AC-8.1 (timeout path): when the in-flight task outlasts the
        budget, ``drain(timeout)`` cancels it and records its final
        status as ``interrupted`` (``interrupted_after_drain`` of them).
    """
    # --- Case #1: AC-8.1 graceful-drain happy path ----------------------
    if expected_drained_count is not None:
        # Build a pool of ``running_count`` tasks that each finish well
        # inside the drain budget, then call ``drain`` and check that the
        # executor reports exactly ``expected_drained_count`` drained
        # tasks.
        async def _exercise() -> None:
            handles = []
            rc = int(running_count)
            for _ in range(rc):
                handles.append(await submit(_sleep_coro(0.05)))
            result = await drain(timeout=float(drain_timeout))
            assert result.drained_count == expected_drained_count, (
                f"FR-08 AC-8.1: drain should have waited for all "
                f"{rc} in-flight tasks; got drained_count="
                f"{result.drained_count}"
            )
            for h in handles:
                # GREEN TODO: ``submit`` MUST return a handle with
                # ``.status`` set to ``"done"`` after a successful drain.
                assert getattr(h, "status", None) == "done", (
                    f"FR-08 AC-8.1: every drained task must end in "
                    f"status 'done'; got {getattr(h, 'status', None)!r}"
                )

        _run(_exercise())
        return

    # --- Case #2: AC-8.1 graceful-drain interrupt-on-timeout ------------
    if interrupted_after_drain is not None:
        # Spawn one task that sleeps far longer than the drain budget,
        # call ``drain(timeout=very_small)``, then assert that exactly
        # ``interrupted_after_drain`` task was interrupted and its status
        # stamped ``expected_status`` (= "interrupted").
        async def _exercise() -> None:
            handle = await submit(_sleep_coro(60.0))
            # Budget shorter than the task's 60s sleep → drain MUST
            # cancel the task and stamp it ``interrupted``.
            result = await drain(timeout=0.1)
            assert result.interrupted_count == interrupted_after_drain, (
                f"FR-08 AC-8.1: drain exceeded its budget; "
                f"interrupted_count should equal {interrupted_after_drain}, "
                f"got {result.interrupted_count}"
            )
            assert getattr(handle, "status", None) == expected_status, (
                f"FR-08 AC-8.1: timed-out drain must stamp interrupted "
                f"status={expected_status!r}; got "
                f"{getattr(handle, 'status', None)!r}"
            )

        _run(_exercise())
        return


# ---------------------------------------------------------------------------
# Case #3: AC-8.3 — task timeout kills orphan subprocess
# ---------------------------------------------------------------------------


def test_task_timeout_kills_orphan_subprocess():
    """FR-08 AC-8.3 — ``run_with_timeout`` must terminate the subprocess.

    With ``task_timeout=0.5`` and a ``command="sleep 10"`` that would
    otherwise outlive the budget, ``run_with_timeout`` MUST kill the
    subprocess (``process.kill()`` + ``await process.wait()``) and report
    ``expected_status == "timeout"``. The process MUST NOT survive — i.e.
    ``expected_orphan_pids == 0`` (no orphan subprocesses).
    """
    task_timeout = 0.5
    command = "sleep 10"
    expected_status = "timeout"
    expected_orphan_pids = "0"

    async def _exercise() -> None:
        # GREEN TODO: ``run_with_timeout(coro, timeout)`` MUST enforce
        # the timeout via ``asyncio.wait_for``; on expiry the underlying
        # subprocess MUST be killed and ``await``ed so no orphan PID
        # survives.
        result = await run_with_timeout(_spawned_subprocess_coro(),
                                        timeout=task_timeout)
        assert result.status == expected_status, (
            f"FR-08 AC-8.3: task exceeding the {task_timeout}s timeout "
            f"must end in status={expected_status!r}; got "
            f"{getattr(result, 'status', None)!r}"
        )
        # No orphan PIDs should remain — ``process.kill()`` was issued
        # AND ``await process.wait()`` returned.
        orphan_count = len(getattr(result, "orphan_pids", []) or [])
        assert str(orphan_count) == expected_orphan_pids, (
            f"FR-08 AC-8.3 / NFR03-AC-N3.5: timeout path must leave "
            f"zero orphan subprocesses; got {orphan_count} orphan pids"
        )

    _run(_exercise())


# ---------------------------------------------------------------------------
# Case #4: AC-8.4 — CancelledError propagates
# ---------------------------------------------------------------------------


def test_cancelled_error_propagates():
    """FR-08 AC-8.4 — ``asyncio.CancelledError`` must propagate upward.

    With ``cancel_after=0.1`` a task that sleeps for longer is cancelled
    while it is still running. The cancellation MUST surface as a real
    :class:`asyncio.CancelledError` (``expected_error_class``) to the
    caller — i.e. no ``except Exception:`` swallows it (NFR-03 / SPEC.md
    line 150).
    """
    cancel_after = 0.1
    expected_error_class = "CancelledError"

    async def _exercise() -> None:
        handle = await submit(_sleep_coro(60.0))
        # Let the task start, then cancel it.
        await asyncio.sleep(float(cancel_after))
        handle.cancel()
        try:
            await handle.wait()
        except Exception as exc:  # noqa: BLE001 — we want to inspect the class
            assert exc.__class__.__name__ == expected_error_class, (
                f"FR-08 AC-8.4 / NFR03-AC-N3.1: cancellation must surface "
                f"as {expected_error_class}; got {exc.__class__.__name__}"
            )
            return
        # If no exception propagated, AC-8.4 has been violated — the
        # ``except Exception`` somewhere swallowed the cancellation.
        pytest.fail(
            "FR-08 AC-8.4: CancelledError was swallowed — expected "
            "asyncio.CancelledError to propagate upward."
        )

    _run(_exercise())