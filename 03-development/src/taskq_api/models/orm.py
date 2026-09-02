"""[FR-01] SQLAlchemy ORM tables — L1 (models).

Declarative tables for tasks, task_results, api_keys, tags, rate_buckets.
This module defines the schema; no business logic lives here.

Citations:
    - SPEC.md §3 FR-01 (tasks CRUD shape)
    - SPEC.md §5.2 (table catalog)
    - SAD.md §2.5, §3.4 (persistence model + revisions)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship

Base = declarative_base()


_TASK_STATUSES = ("pending", "running", "done", "failed", "timeout", "interrupted")


def status_values() -> tuple[str, ...]:
    """Return the canonical task-status vocabulary.

    Returns:
        Tuple of status strings.

    Citations: SPEC.md §3 FR-02 (state machine), SAD.md §3.3.
    """
    return _TASK_STATUSES


# Association table for tasks <-> tags (FR-02 / v2 migration).
task_tags = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", String(36), ForeignKey("tasks.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Task(Base):
    """[FR-01] A single task resource (the row behind `/v1/tasks`)."""

    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    command = Column(String(1024), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    results = relationship(
        "TaskResult",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TaskResult(Base):
    """[FR-02] One execution of a task (FR-07 v3 schema split)."""

    __tablename__ = "task_results"

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    exit_code = Column(Integer, nullable=False, default=0)
    stdout_tail = Column(String(4096), nullable=False, default="")
    stderr_tail = Column(String(4096), nullable=False, default="")
    duration_ms = Column(Integer, nullable=False, default=0)
    finished_at = Column(DateTime(timezone=True), nullable=False)

    task = relationship("Task", back_populates="results")


class ApiKey(Base):
    """[FR-03] Hashed API-key row (`key_hash`, never plaintext)."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    scope = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class Tag(Base):
    """[FR-02] Free-form label attached to tasks via task_tags."""

    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True)


class RateBucket(Base):
    """[FR-05] Per-key token-bucket state (row-locked refill).

    ``key_id`` is the principal's SHA-256 hash prefix (the value
    :class:`taskq_api.service.auth.Principal` carries), not the
    ``api_keys.id`` surrogate — the auth chokepoint resolves a principal,
    so the hash prefix is the identifier available where the bucket is
    consulted.

    Citations: SPEC.md line 119 (bucket state in the DB), SPEC.md line 313.
    """

    __tablename__ = "rate_buckets"

    # Annotated (``Mapped``) columns — the repository reads and writes
    # these values as plain ``str`` / ``int`` / ``datetime``, and the
    # annotation is what lets a type checker see them that way instead of
    # as ``Column[...]`` descriptors.
    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_refill: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
