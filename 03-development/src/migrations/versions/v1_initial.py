"""[FR-07] v1_initial — create ``tasks`` and ``api_keys``.
# pragma: no error-handling  # pure data/constants — no I/O to handle

Revision ID: v1
Revises:     base
Create Date: 2026-09-02 00:00:00.000000

The ``tasks`` table carries a ``result_json`` blob column that the v3
revision will split into the independent ``task_results`` table (this
is the FR-07 AC-7.3 / AC-7.5 round-trip data-migration surface). The
``api_keys`` table mirrors :class:`taskq_api.models.orm.ApiKey` but is
created here from scratch because the v1 revision is the first user
of the schema.

Downgrade drops both tables — no ``op.execute("DROP TABLE ...")``
shortcut substitutes for the real ``op.drop_table(...)`` ops (NFR-02).

Citations:
    - SPEC.md §5.2 (schema)
    - SAD.md §3.4 (revision chain)
    - NFR-02 (no destructive shortcuts)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``tasks`` (with ``result_json``) and ``api_keys``."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("result_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    """Drop ``api_keys`` (and its unique index) then ``tasks``."""
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("tasks")