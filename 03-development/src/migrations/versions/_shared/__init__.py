"""[FR-07] Shared helpers used by every alembic revision script.

This module is a sub-package so it is **not** picked up by alembic's
``ScriptDirectory._list_py_dir`` walk (alembic only scans the top level
of the ``versions/`` directory by default — ``recursive_version_locations
= False``). It still imports as ``migrations.versions._shared`` because
Python treats a directory with an ``__init__.py`` as a sub-package.

The three revision scripts (v1_initial, v2_tags, v3_split_results) share
a handful of column shapes and row-copy primitives. Keeping them in one
hub module satisfies the SAD.md §2.1 community-hub requirement and
keeps each revision focused on its own upgrade/downgrade pair.

Citations:
    - SPEC.md §5.2 (schema)
    - SAD.md §2.1 (hub-per-directory)
"""

from __future__ import annotations

import sqlalchemy as sa


# ---------------------------------------------------------------------------
# Column defaults — keep the FR-07 migrations reversible by reusing the
# same column shapes on both sides of the v3 split / merge.
# ---------------------------------------------------------------------------


def task_id_column() -> sa.Column:
    """Return the canonical ``task_id`` column used on ``task_results``.

    Matches :class:`taskq_api.models.orm.TaskResult.task_id`.
    """
    return sa.Column(
        "task_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id"),
        nullable=False,
        index=True,
    )


def utc_now(name: str = "created_at") -> sa.Column:
    """Return a UTC ``DateTime(timezone=True)`` column with a server default."""
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )


# ---------------------------------------------------------------------------
# Row-copy primitives — the v3 split / merge operations call these to
# move data between ``tasks.result_json`` and ``task_results`` without
# silently dropping any column value.
# ---------------------------------------------------------------------------


def copy_rows(
    bind,
    *,
    source_table: sa.Table,
    target_table: sa.Table,
    column_map: dict,
) -> int:
    """Copy rows from ``source_table`` to ``target_table`` column-by-column.

    ``column_map`` maps each ``target_table`` column name to the
    corresponding ``source_table`` column name (or a literal expression
    AST node). Returns the number of rows copied so callers can log /
    assert the count when FR-07 needs the exact number preserved.

    Args:
        bind: SQLAlchemy connection or engine used to execute the copy.
        source_table: Table to read rows from.
        target_table: Table to insert into.
        column_map: ``{target_col: source_col_or_expr}`` mapping.

    Returns:
        Number of rows inserted into ``target_table``.
    """
    src_cols = ", ".join(
        sa.sql.column(src if isinstance(src, str) else src)
        for src in column_map.values()
    )
    tgt_cols = ", ".join(sa.sql.column(t) for t in column_map)
    select_stmt = sa.select(*[
        sa.sql.column(src if isinstance(src, str) else src)
        for src in column_map.values()
    ]).select_from(source_table)
    insert_stmt = target_table.insert().from_select(
        list(column_map.keys()),
        select_stmt,
    )
    result = bind.execute(insert_stmt)
    if result.rowcount is not None:
        return int(result.rowcount)
    count_stmt = sa.select(sa.func.count()).select_from(target_table)
    return int(bind.execute(count_stmt).scalar_one())


def json_loads_safe(raw: str | None) -> dict | None:
    """Parse a JSON string into a dict, returning ``None`` on error.

    Used by the v3 downgrade path when merging ``task_results`` rows
    back into ``tasks.result_json`` — a malformed ``stdout_tail`` /
    ``stderr_tail`` value must NOT crash the downgrade (the AC-7.5
    round-trip test runs downgrade -1 against real data).
    """
    import json

    if raw is None or raw == "":
        return None
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return loaded if isinstance(loaded, dict) else None