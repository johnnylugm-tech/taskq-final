"""[FR-07] v2_tags — add ``tags``, ``task_tags`` and UNIQUE on ``tasks.name``.

Revision ID: v2
Revises:     v1
Create Date: 2026-09-02 00:00:01.000000

v2 introduces the free-form ``tags`` / ``task_tags`` M2M pair (FR-02)
and a UNIQUE index on ``tasks.name`` (FR-01 prevents duplicate names).
The downgrade drops only what v2 added; the v1 ``tasks`` / ``api_keys``
rows are untouched (AC-7.2).

Citations:
    - SPEC.md §5.2 (schema)
    - SAD.md §3.4 (revision chain)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "v2"
down_revision = "v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``tags``, ``task_tags``, and a UNIQUE index on ``tasks.name``."""
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )

    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id"), primary_key=True),
    )

    op.create_index(
        "uq_tasks_name",
        "tasks",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the UNIQUE index, ``task_tags``, then ``tags`` — leaving v1 data intact."""
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")