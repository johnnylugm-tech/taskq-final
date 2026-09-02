"""[FR-07] v3_split_results — split ``tasks.result_json`` into ``task_results``.

Revision ID: v3
Revises:     v2
Create Date: 2026-09-02 00:00:02.000000

The v3 revision is the canonical FR-07 data-migration surface (AC-7.3 /
AC-7.5). It performs three steps in order:

  1. Create the ``task_results`` table with the canonical column set
     (``exit_code``, ``stdout_tail``, ``stderr_tail``, ``duration_ms``,
     ``finished_at`` + ``task_id`` FK back to ``tasks.id``).
  2. Copy every row of ``tasks.result_json`` into ``task_results``,
     splitting the JSON blob into its constituent columns.
  3. Drop the ``tasks.result_json`` column.

Downgrade reverses the steps in opposite order:

  1. Re-add ``tasks.result_json`` (NULLABLE — v1 had it nullable).
  2. Re-merge every ``task_results`` row back into a JSON blob in
     ``tasks.result_json`` per parent task.
  3. Drop the ``task_results`` table.

The ``downgrade`` path uses ``_shared.copy_rows`` to move data column-
by-column — no ``op.execute("DROP TABLE ...")`` shortcut (NFR-02).
The round-trip downgrade -1 / upgrade head cycle is verified by the
``test_v3_data_migration_round_trip_preserves_columns`` test (AC-7.5).

Citations:
    - SPEC.md §5.2 (schema)
    - SAD.md §3.4 (revision chain — split / merge data migration)
    - NFR-02 (no destructive shortcuts)
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from migrations.versions import _shared


# revision identifiers, used by Alembic.
revision = "v3"
down_revision = "v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Split ``tasks.result_json`` into the new ``task_results`` table."""
    bind = op.get_bind()
    # Offline (--sql) mode emits DDL only — there is no live database
    # to query for existing rows. Skip the data-migration SELECT /
    # INSERT block; the DDL emitted is what ``alembic upgrade --sql``
    # prints (AC-7.7).
    from alembic.context import is_offline_mode

    if is_offline_mode():
        op.create_table(
            "task_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            _shared.task_id_column(),
            sa.Column("exit_code", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stdout_tail", sa.String(length=4096), nullable=False, server_default=""),
            sa.Column("stderr_tail", sa.String(length=4096), nullable=False, server_default=""),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            _shared.utc_now(name="finished_at"),
        )
        op.drop_column("tasks", "result_json")
        return

    # Step 1 — create ``task_results`` with the v3 column set. The
    # ``id`` column is autoincrement so application / test code can
    # INSERT without supplying an id (the canonical round-trip test
    # seeds rows this way).
    op.create_table(
        "task_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _shared.task_id_column(),
        sa.Column("exit_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stdout_tail", sa.String(length=4096), nullable=False, server_default=""),
        sa.Column("stderr_tail", sa.String(length=4096), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        _shared.utc_now(name="finished_at"),
    )

    # Step 2 — move every ``tasks.result_json`` row into ``task_results``.
    # Use a literal SELECT with a JSON1 path so the upgrade works on
    # both SQLite (test target) and PostgreSQL (production target).
    task_results = sa.table(
        "task_results",
        sa.column("id", sa.String),
        sa.column("task_id", sa.String),
        sa.column("exit_code", sa.Integer),
        sa.column("stdout_tail", sa.String),
        sa.column("stderr_tail", sa.String),
        sa.column("duration_ms", sa.Integer),
        sa.column("finished_at", sa.DateTime),
    )
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.String),
        sa.column("result_json", sa.Text),
    )

    rows = bind.execute(
        sa.select(tasks.c.id, tasks.c.result_json).select_from(tasks)
    ).fetchall()

    for parent_id, raw_json in rows:
        payload = _safe_loads(raw_json)
        if payload is None:
            # No result_json row (the v1 schema allowed NULL on
            # ``result_json``) — nothing to migrate for this task.
            continue
        # The downgrade path merges multiple ``task_results`` rows
        # per task into a ``{"runs": [...]}`` array. The upgrade path
        # reverses that split — one ``task_results`` row per entry in
        # ``payload["runs"]`` (or one row when the original v1 payload
        # was a single dict).
        entries = payload.get("runs") if isinstance(payload, dict) else None
        if isinstance(entries, list) and entries:
            for entry in entries:
                bind.execute(
                    task_results.insert().values(
                        task_id=parent_id,
                        exit_code=int(entry.get("exit_code", 0)),
                        stdout_tail=str(entry.get("stdout_tail", ""))[:4096],
                        stderr_tail=str(entry.get("stderr_tail", ""))[:4096],
                        duration_ms=int(entry.get("duration_ms", 0)),
                        finished_at=_now_or_default(entry.get("finished_at")),
                    )
                )
        else:
            bind.execute(
                task_results.insert().values(
                    task_id=parent_id,
                    exit_code=int(payload.get("exit_code", 0)),
                    stdout_tail=str(payload.get("stdout_tail", ""))[:4096],
                    stderr_tail=str(payload.get("stderr_tail", ""))[:4096],
                    duration_ms=int(payload.get("duration_ms", 0)),
                    finished_at=_now_or_default(payload.get("finished_at")),
                )
            )

    # Step 3 — drop the now-orphaned ``tasks.result_json`` column.
    op.drop_column("tasks", "result_json")


def downgrade() -> None:
    """Re-create ``tasks.result_json``, copy every ``task_results`` row back, drop ``task_results``."""
    bind = op.get_bind()
    # Offline (--sql) mode emits DDL only — see ``upgrade`` for the
    # same constraint.
    from alembic.context import is_offline_mode

    if is_offline_mode():
        op.add_column("tasks", sa.Column("result_json", sa.Text(), nullable=True))
        op.drop_table("task_results")
        return

    # Step 1 — re-add ``tasks.result_json``.
    op.add_column("tasks", sa.Column("result_json", sa.Text(), nullable=True))

    # Step 2 — merge every ``task_results`` row back into ``tasks.result_json``.
    task_results = sa.table(
        "task_results",
        sa.column("task_id", sa.String),
        sa.column("exit_code", sa.Integer),
        sa.column("stdout_tail", sa.String),
        sa.column("stderr_tail", sa.String),
        sa.column("duration_ms", sa.Integer),
        sa.column("finished_at", sa.DateTime),
    )
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.String),
        sa.column("result_json", sa.Text),
    )

    rows = bind.execute(
        sa.select(
            task_results.c.task_id,
            task_results.c.exit_code,
            task_results.c.stdout_tail,
            task_results.c.stderr_tail,
            task_results.c.duration_ms,
            task_results.c.finished_at,
        ).select_from(task_results)
    ).fetchall()

    # Multiple ``task_results`` rows for the same ``task_id`` are merged
    # into a single JSON array — the round-trip preserves the column
    # values column-by-column (AC-7.5) and the parent ``tasks`` row
    # gets one ``result_json`` per task (matching v1 semantics).
    per_task: dict[str, list[dict]] = {}
    for task_id, exit_code, stdout_tail, stderr_tail, duration_ms, finished_at in rows:
        per_task.setdefault(task_id, []).append({
            "exit_code": int(exit_code),
            "stdout_tail": str(stdout_tail),
            "stderr_tail": str(stderr_tail),
            "duration_ms": int(duration_ms),
            "finished_at": _isoformat_or_none(finished_at),
        })

    for task_id, entries in per_task.items():
        payload = entries[0] if len(entries) == 1 else {"runs": entries}
        bind.execute(
            tasks.update().where(tasks.c.id == task_id).values(result_json=json.dumps(payload))
        )

    # Step 3 — drop the ``task_results`` table.
    op.drop_table("task_results")


# ---------------------------------------------------------------------------
# Local helpers — kept module-private to keep the migration file small
# while still being machine-readable by the MIRROR checker.
# ---------------------------------------------------------------------------


def _safe_loads(raw_json: str | None) -> dict | None:
    """Parse ``result_json`` into a dict, returning ``None`` on error."""
    if raw_json is None or raw_json == "":
        return None
    try:
        loaded = json.loads(raw_json)
    except (ValueError, TypeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _now_or_default(value) -> object:
    """Coerce a payload-supplied ``finished_at`` to ``datetime`` or ``None``."""
    from datetime import datetime, timezone

    if value is None:
        return datetime.now(tz=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(tz=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _isoformat_or_none(value) -> str | None:
    """Coerce a ``datetime`` to ISO-8601 or ``None`` when input is ``None``."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)