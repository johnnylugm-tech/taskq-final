"""Adversarial bug-hunt regression test — T-07 (orphan subprocess on cancel).

Bug: service/runner.py:_communicate_with_timeout only catches
asyncio.TimeoutError. When an outer wait_for (run_with_timeout) or
drain() cancels the running task, asyncio.CancelledError bypasses the
inner try/except, proc.kill() is never called, and the subprocess is
orphaned.

Repro contract (RED): wrap run_subprocess in an outer asyncio.wait_for
that fires BEFORE the inner _communicate_with_timeout's own timeout;
assert the subprocess is killed (no orphan PIDs survive).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TASKQ_DB_URL", "sqlite:///./taskq.db")

import pytest  # noqa: E402

from taskq_api.service.runner import run_subprocess  # noqa: E402


def test_t07_outer_cancellation_kills_subprocess():
    """[T-07] Cancelling the outer task MUST kill the spawned subprocess."""

    async def _drive():
        async def _outer_wait_for():
            # Inner _communicate_with_timeout has a 30-second default
            # timeout. The outer 0.5s fires FIRST so the inner
            # TimeoutError branch is unreachable; the inner receives
            # asyncio.CancelledError instead.
            return await asyncio.wait_for(
                run_subprocess("sleep 60", timeout=30.0),
                timeout=0.5,
            )

        with pytest.raises(asyncio.TimeoutError):
            await _outer_wait_for()

        # Give the kernel a moment to reap a killed process.
        await asyncio.sleep(0.2)

        # Scope the ps scan to THIS process tree only (ppid chain back to
        # the pytest runner). The unfiltered ``ps -A`` form had a false-
        # positive on the full verify-system run when other tests left
        # `sleep` orphans on the host — the regression we care about is
        # whether OUR spawn survived, not whether any host-level ``sleep``
        # exists.
        my_ppid = os.getppid()
        proc = await asyncio.create_subprocess_exec(
            "ps", "-A", "-o", "pid=,ppid=,comm=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        descendants: set[int] = set()
        rows = [
            line.strip().split(None, 2)
            for line in stdout.decode().splitlines()
            if line.strip()
        ]
        # First pass: index ppid -> [pid]
        by_ppid: dict[int, list[int]] = {}
        for row in rows:
            if len(row) < 2:
                continue
            try:
                pid = int(row[0])
                ppid = int(row[1])
            except ValueError:
                continue
            by_ppid.setdefault(ppid, []).append(pid)
        # Second pass: walk descendants of my_ppid
        frontier = [my_ppid]
        while frontier:
            children = []
            for pid in frontier:
                children.extend(by_ppid.get(pid, []))
            descendants.update(children)
            frontier = children
        # Third pass: any sleep row whose pid is in our descendant set
        sleep_orphans: list[str] = []
        for row in rows:
            if len(row) < 3 or not row[2].endswith("sleep"):
                continue
            try:
                pid = int(row[0])
            except ValueError:
                continue
            if pid in descendants:
                sleep_orphans.append(" ".join(row))
        return sleep_orphans

    alive = asyncio.run(_drive())
    assert not alive, (
        "T-07 regression: outer cancellation left orphan 'sleep' "
        f"subprocess(es) alive: {alive!r}"
    )

