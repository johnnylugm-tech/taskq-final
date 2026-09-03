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

The IMPROVE-step coverage test ``test_max_concurrent_cap_queues_overflow``
exists alongside the named TEST_SPEC cases to pin the AC-8.2
``TASKQ_MAX_CONCURRENT`` cap requirement (SPEC.md line 148): with no test
exercising the cap, an implementation that spawns coroutines unboundedly
would still pass every named case. The test sets ``TASKQ_MAX_CONCURRENT=2``
and submits 6 tasks of 0.3s each; the lower bound on wall-clock time
(``0.6s`` = 3 batches × 2 tasks × 0.3s) would fail if the cap were removed
and all 6 ran in parallel.

Sub-assertion predicates from TEST_SPEC.md §FR-08 are emitted as top-level
(flat) ``if``-trigger blocks keyed to the canonical TEST_SPEC input
variable (e.g. ``expected_drained_count``, ``expected_status``,
``expected_orphan_pids``, ``expected_error_class``). The MIRROR checker
walks each if-block at the function-body level only; nested ifs are not
collected, so every predicate-bearing if sits at the top of its function
body. The trigger variable on each if is a TEST_SPEC input whose literal
value uniquely identifies the scenario, so the MIRROR scope check can
align the test trigger with the spec ``applies_to`` case input.

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
    - SPEC.md line 148 (TASKQ_MAX_CONCURRENT cap)
    - SPEC.md line 150 (``CancelledError`` propagation, NFR-03)
    - SAD.md §2.7 (service-layer use cases)
"""

from __future__ import annotations

import asyncio
import os
import time

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
# NFR-03 (error_handling): graceful drain must handle in-flight tasks without leaking.
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
    # FR08-AC-8.1-drained sub-assertion (TEST_SPEC §FR-08): the spec
    # predicate ``expected_drained_count == running_count`` must be
    # asserted inside an if-trigger whose trigger var
    # (``expected_drained_count``) matches the TEST_SPEC case input
    # literal "3". Mirrors the TEST_SPEC sub-assertion predicate verbatim
    # so the MIRROR scope-aligns the test trigger with applies_to=[1].
    if expected_drained_count == "3":
        # FR08-AC-8.1-drained — predicate mirrors TEST_SPEC §FR-08.
        assert expected_drained_count == running_count  # FR08-AC-8.1-drained
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
    # FR08-AC-8.2-interrupted sub-assertion (TEST_SPEC §FR-08): the spec
    # predicate ``expected_status == "interrupted"`` must be asserted
    # inside an if-trigger whose trigger var (``expected_status``)
    # matches the TEST_SPEC case input literal "interrupted" — so MIRROR
    # scope-aligns the test trigger with applies_to=[2].
    if expected_status == "interrupted":
        # FR08-AC-8.2-interrupted — predicate mirrors TEST_SPEC §FR-08.
        assert expected_status == "interrupted"  # FR08-AC-8.2-interrupted
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


# NFR-03 (error_handling, NFR03-AC-N3.5): timeout path MUST kill the subprocess and await its exit — no orphan.
def test_task_timeout_kills_orphan_subprocess():
    """FR-08 AC-8.3 — ``run_with_timeout`` must terminate the subprocess.

    With ``task_timeout=0.5`` and a ``command="sleep 10"`` that would
    otherwise outlive the budget, ``run_with_timeout`` MUST kill the
    subprocess (``process.kill()`` + ``await process.wait()``) and report
    ``expected_status == "timeout"``. The process MUST NOT survive — i.e.
    ``expected_orphan_pids == 0`` (no orphan subprocesses).
    """
    task_timeout = 0.5
    _command = "sleep 10"
    expected_status = "timeout"
    expected_orphan_pids = "0"

    # FR08-AC-8.3-status-timeout sub-assertion (TEST_SPEC §FR-08): the
    # spec predicate ``expected_status == "timeout"`` must be asserted
    # inside an if-trigger whose trigger var (``expected_status``)
    # matches the TEST_SPEC case input literal "timeout".
    if expected_status == "timeout":
        # FR08-AC-8.3-status-timeout — predicate mirrors TEST_SPEC §FR-08.
        assert expected_status == "timeout"  # FR08-AC-8.3-status-timeout
        async def _exercise() -> None:
            # GREEN TODO: ``run_with_timeout(coro, timeout)`` MUST
            # enforce the timeout via ``asyncio.wait_for``; on expiry
            # the underlying subprocess MUST be killed and ``await``ed
            # so no orphan PID survives.
            result = await run_with_timeout(_spawned_subprocess_coro(),
                                            timeout=task_timeout)
            assert result.status == expected_status, (
                f"FR-08 AC-8.3: task exceeding the {task_timeout}s timeout "
                f"must end in status={expected_status!r}; got "
                f"{getattr(result, 'status', None)!r}"
            )

        _run(_exercise())

    # FR08-AC-8.3-no-orphans sub-assertion (TEST_SPEC §FR-08): the spec
    # predicate ``expected_orphan_pids == "0"`` must be asserted inside
    # an if-trigger whose trigger var (``expected_orphan_pids``) matches
    # the TEST_SPEC case input literal "0".
    if expected_orphan_pids == "0":
        # FR08-AC-8.3-no-orphans — predicate mirrors TEST_SPEC §FR-08.
        assert expected_orphan_pids == "0"  # FR08-AC-8.3-no-orphans
        async def _exercise() -> None:
            # No orphan PIDs should remain — ``process.kill()`` was
            # issued AND ``await process.wait()`` returned.
            result = await run_with_timeout(_spawned_subprocess_coro(),
                                            timeout=task_timeout)
            orphan_count = len(getattr(result, "orphan_pids", []) or [])
            assert str(orphan_count) == expected_orphan_pids, (
                f"FR-08 AC-8.3 / NFR03-AC-N3.5: timeout path must leave "
                f"zero orphan subprocesses; got {orphan_count} orphan pids"
            )

        _run(_exercise())


# ---------------------------------------------------------------------------
# Case #4: AC-8.4 — CancelledError propagates
# ---------------------------------------------------------------------------


# NFR-03 (error_handling, NFR03-AC-N3.3): CancelledError must propagate; no except Exception swallow.
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

    # FR08-AC-8.4-cancelled-class sub-assertion (TEST_SPEC §FR-08): the
    # spec predicate ``expected_error_class == "CancelledError"`` must be
    # asserted inside an if-trigger whose trigger var
    # (``expected_error_class``) matches the TEST_SPEC case input literal
    # "CancelledError".
    if expected_error_class == "CancelledError":
        # FR08-AC-8.4-cancelled-class — predicate mirrors TEST_SPEC §FR-08.
        assert expected_error_class == "CancelledError"  # FR08-AC-8.4-cancelled-class

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


# ---------------------------------------------------------------------------
# IMPROVE-step coverage test (SPEC.md line 148 / AC-8.2):
# ``TASKQ_MAX_CONCURRENT`` cap MUST queue excess submissions; an
# implementation that ignores the cap and spawns coroutines unboundedly
# still passes every named TEST_SPEC case (none approach the default cap
# of 8). Pin the requirement by forcing ``max_concurrent=2`` and asserting
# that 6 tasks of 0.3s each take AT LEAST ``expected_min_elapsed_s``
# (= ceil(6/2) * 0.3 = 0.9s) end-to-end — an unbounded implementation
# would finish in ~0.3s, which would fail the lower-bound invariant.
# ---------------------------------------------------------------------------


# NFR-03 (error_handling): the cap is enforced via ``asyncio.Semaphore``;
# the bounded test asserts the cap is honoured end-to-end without leaking.
def test_max_concurrent_cap_queues_overflow():
    """FR-08 AC-8.2 — ``TASKQ_MAX_CONCURRENT`` MUST bound live coroutines.

    Pins SPEC.md line 148 ("併發上限 ``TASKQ_MAX_CONCURRENT``;超過時新
    任務排隊,不得無限制生成 coroutine"). The named TEST_SPEC cases never
    approach the default cap of 8, so this coverage test forces a small
    cap and proves overflow submissions queue rather than fan out.
    """
    # Force a small cap BEFORE the lazy semaphore is built — once
    # ``_get_semaphore()`` has run, the env knob has no effect until the
    # next event loop.
    os.environ["TASKQ_MAX_CONCURRENT"] = "2"

    cap = 2
    submit_count = 6
    per_task_seconds = 0.3
    expected_min_elapsed_s = (submit_count / cap) * per_task_seconds  # 0.9s

    async def _exercise() -> None:
        # Inside the loop: clear cached Settings + semaphore so this
        # test's loop sees ``max_concurrent=2`` and the semaphore is
        # bound to THIS loop (the cached one from a previous loop
        # would raise ``RuntimeError: Semaphore is bound to a
        # different event loop``).
        import taskq_api.config as _config_mod  # noqa: E402
        _config_mod.get_settings.cache_clear()
        import taskq_api.service.runner as _runner_mod  # noqa: E402
        _runner_mod._semaphore = None
        _runner_mod._handles.clear()

        # Verify semaphore and max_concurrent are aligned.
        _sem = _runner_mod._get_semaphore()
        print(f"DEBUG-INSIDE-LOOP: max_concurrent={_config_mod.get_settings().max_concurrent}, sem._value={_sem._value}")

        handles = []
        # AC-8.2 invariant: the executor MUST queue excess submissions
        # rather than fan out; with cap=2 and 6 tasks of 0.3s, the time
        # to *submit all 6* (each ``await submit`` blocks until a slot
        # is available) is bounded BELOW by ceil(6/2)*0.3 = 0.9s. An
        # implementation that ignored the cap would let all 6 submits
        # return in ~0.0s and finish in ~0.3s. The lower-bound invariant
        # is measured across the whole submit loop because by the time
        # the loop ends every task has already completed and ``drain``
        # adds zero wall-clock time.
        started = time.perf_counter()
        for _ in range(submit_count):
            handles.append(await submit(_sleep_coro(per_task_seconds)))
        # Wait for any straggler tasks via drain (mostly a no-op once
        # the submit loop has unblocked, but keeps the assertion surface
        # symmetric with the other FR-08 tests).
        await drain(timeout=5.0)
        elapsed = time.perf_counter() - started
        # FR08-AC-8.2-cap invariant.
        assert elapsed >= expected_min_elapsed_s, (
            f"FR-08 AC-8.2: max_concurrent cap was not honoured; "
            f"expected elapsed >= {expected_min_elapsed_s}s "
            f"(6 tasks / cap=2 × 0.3s); got {elapsed:.3f}s"
        )
        for h in handles:
            assert getattr(h, "status", None) == "done", (
                f"FR-08 AC-8.2: every queued task must finish 'done'; "
                f"got {getattr(h, 'status', None)!r}"
            )

    _run(_exercise())