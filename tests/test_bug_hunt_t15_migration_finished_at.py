"""Adversarial bug-hunt regression test — T-15 (silent finished_at substitution).

Bug: migrations/versions/v3_split_results.py:_now_or_default silently
substitutes datetime.now() when the v1 result_json payload lacks a
finished_at field (or has an invalid one). The original timestamp is
lost; downstream queries that ORDER BY finished_at are corrupted.

Repro contract (RED): seed a v2 DB with a task whose result_json has
no finished_at, apply v3, assert the resulting task_results.finished_at
EITHER raises OR matches the parent task's created_at (NOT now()).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TASKQ_DB_URL", "sqlite:///./taskq.db")


def test_t15_missing_finished_at_does_not_substitute_now(tmp_path):
    """[T-15] v3 upgrade must NOT silently substitute now() for missing finished_at."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, text

    import migrations.versions.v1_initial as v1
    import migrations.versions.v2_tags as v2
    import migrations.versions.v3_split_results as v3

    engine = create_engine(f"sqlite:///{tmp_path}/t15.db", future=True)

    # Bring schema to v2.
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        original_op = v1.op
        v1.op = op
        try:
            v1.upgrade()
        finally:
            v1.op = original_op
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        original_op = v2.op
        v2.op = op
        try:
            v2.upgrade()
        finally:
            v2.op = original_op

    # Plant a parent task whose created_at is anchored in the past so we
    # can distinguish 'now()' (the bug) from 'parent.created_at' (the fix).
    anchored_created_at = datetime(
        2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc,
    )
    payload_without_finished_at = json.dumps({
        "exit_code": 0,
        "stdout_tail": "no-finished-at",
        "stderr_tail": "",
        "duration_ms": 0,
        # NOTE: 'finished_at' deliberately omitted.
    })

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (id, name, command, status, created_at, result_json) "
                "VALUES (:id, :n, :c, :s, :ts, :rj)"
            ),
            {
                "id": "t-no-finished", "n": "no-finished-at",
                "c": "echo z", "s": "pending",
                "ts": anchored_created_at,
                "rj": payload_without_finished_at,
            },
        )

    # Apply v3 (online mode).
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        original_op = v3.op
        original_offline = v3.is_offline_mode
        v3.op = op
        v3.is_offline_mode = lambda: False
        try:
            v3.upgrade()
        finally:
            v3.op = original_op
            v3.is_offline_mode = original_offline

    # T-15 contract: the resulting finished_at MUST NOT be 'now()' —
    # either raise, or use the parent task's created_at as a fallback.
    with engine.connect() as conn:
        finished_at_raw = conn.execute(text(
            "SELECT finished_at FROM task_results WHERE task_id = 't-no-finished'"
        )).scalar_one()

    assert finished_at_raw is not None, (
        "v3 left finished_at NULL for an entry that lacked one — "
        "either raise or fallback to parent.created_at; silent NULL "
        "is no better than silent now()."
    )

    # SQLite round-trips DateTime columns as naive ISO strings.
    if isinstance(finished_at_raw, str):
        finished_at = datetime.fromisoformat(finished_at_raw).replace(
            tzinfo=timezone.utc,
        )
    elif finished_at_raw.tzinfo is None:
        finished_at = finished_at_raw.replace(tzinfo=timezone.utc)
    else:
        finished_at = finished_at_raw

    # Anything within 60s of "right now" is the bug — wall-clock substitution.
    drift = abs((datetime.now(timezone.utc) - finished_at).total_seconds())
    assert drift > 60, (
        "T-15 regression: v3 upgrade substituted datetime.now() for "
        f"the missing finished_at (drift={drift:.0f}s from now). The "
        "fix must use the parent task's created_at or raise."
    )

    # The fix uses parent.created_at — assert it matches.
    assert finished_at == anchored_created_at, (
        "T-15 regression: finished_at does not match parent.created_at "
        f"({finished_at.isoformat()} vs {anchored_created_at.isoformat()}). "
        "The fix must fall back to the parent task's created_at."
    )
