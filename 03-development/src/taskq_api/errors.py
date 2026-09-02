"""[FR-10] RFC 7807 problem+json + [NFR-04] secret redaction.

Independence module: depends only on stdlib. Owns the domain-error classes
and the canonical error-body builder.

Citations:
    - SPEC.md §3 FR-10 (problem+json contract)
    - SPEC.md §4 NFR-02 (no SQL/shell-injection in error detail)
    - SPEC.md §4 NFR-04 (secret redaction)
    - SAD.md §2.4 (logical constraints)
"""

from __future__ import annotations

import re
import uuid
from typing import Optional


# Regex covering sk-*, token=, Bearer, postgres URLs (NFR-04).
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(?:ql)?://\S+)"
)


class Problem(Exception):
    """[FR-10] Base RFC 7807 problem+json error.

    Carries the four required fields (`type`, `title`, `status`, `detail`)
    plus an optional `instance` and `correlation_id` for tracing.

    Citations: SPEC.md §3 FR-10.
    """

    def __init__(
        self,
        type_: str,
        title: str,
        status: int,
        detail: str,
        instance: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(detail)
        self.type = type_
        self.title = title
        self.status = status
        self.detail = detail
        self.instance = instance
        self.correlation_id = correlation_id or new_correlation_id()


class ValidationProblem(Problem):
    """[FR-10] 422 — validation rule violation."""

    def __init__(self, detail: str = "request body failed validation") -> None:
        super().__init__(
            type_="/errors/validation",
            title="Validation failed",
            status=422,
            detail=detail,
        )


class UnauthenticatedProblem(Problem):
    """[FR-10, FR-03] 401 — missing or invalid API key."""

    def __init__(self, detail: str = "missing or invalid API key") -> None:
        super().__init__(
            type_="/errors/unauthenticated",
            title="Unauthenticated",
            status=401,
            detail=detail,
        )


class ForbiddenProblem(Problem):
    """[FR-10, FR-04] 403 — insufficient scope.

    Citations: SPEC.md §4 NFR-02 (body must not disclose existence).
    """

    def __init__(self, detail: str = "insufficient scope") -> None:
        super().__init__(
            type_="/errors/forbidden",
            title="Forbidden",
            status=403,
            detail=detail,
        )


class NotFoundProblem(Problem):
    """[FR-10, FR-01] 404 — resource not found."""

    def __init__(self, detail: str = "resource not found") -> None:
        super().__init__(
            type_="/errors/not-found",
            title="Not Found",
            status=404,
            detail=detail,
        )


class ConflictProblem(Problem):
    """[FR-10] 409 — uniqueness / state conflict."""

    def __init__(self, detail: str = "conflict") -> None:
        super().__init__(
            type_="/errors/conflict",
            title="Conflict",
            status=409,
            detail=detail,
        )


class RateLimitedProblem(Problem):
    """[FR-10, FR-05] 429 — bucket exhausted."""

    def __init__(self, detail: str = "rate limit exceeded") -> None:
        super().__init__(
            type_="/errors/rate-limited",
            title="Too Many Requests",
            status=429,
            detail=detail,
        )


def problem_body(problem: Problem) -> dict:
    """[FR-10] Serialize a :class:`Problem` to its wire shape.

    Citations: SPEC.md §3 FR-10 (RFC 7807 fields).
    """
    return {
        "type": problem.type,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": problem.instance,
    }


def new_correlation_id() -> str:
    """Return a fresh correlation id (UUID4 hex)."""
    return uuid.uuid4().hex


def redact_secrets(text: str) -> str:
    """[NFR-04] Replace every line matching the secret regex with `[REDACTED]`.

    Citations: SPEC.md §4 NFR-04 (replaces full line, not just the match).
    """
    if not text:
        return text
    return _SECRET_RE.sub("[REDACTED]", text)
