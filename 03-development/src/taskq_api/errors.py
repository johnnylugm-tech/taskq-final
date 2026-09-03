"""[FR-10] RFC 7807 problem+json + [NFR-04] secret redaction.
# pragma: no error-handling  # pure data/constants — no I/O to handle

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
    """[FR-10, FR-05] 429 — bucket exhausted.

    ``retry_after`` is the integer-second hint the transport layer renders
    as the ``Retry-After`` header (SPEC.md line 118); ``None`` means the
    caller did not resolve a wait and no header is emitted.
    """

    def __init__(
        self,
        detail: str = "rate limit exceeded",
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(
            type_="/errors/rate-limited",
            title="Too Many Requests",
            status=429,
            detail=detail,
        )
        self.retry_after = retry_after


def problem_body(problem: Problem) -> dict:
    """[FR-10] Serialize a :class:`Problem` to its wire shape.

    The wire body carries the AC-10.2 contract fields:

        ``type``, ``title``, ``status``, ``detail``, ``instance``,
        ``correlation_id``.

    Citations: SPEC.md §3 FR-10 (AC-10.2 wire shape).
    """
    return {
        "type": problem.type,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": problem.instance,
        "correlation_id": problem.correlation_id,
    }


def new_correlation_id() -> str:
    """Return a fresh correlation id (UUID4 hex)."""
    return uuid.uuid4().hex


def redact_secrets(text: str) -> str:
    """[NFR-04] Replace every match of the secret regex with ``[REDACTED]``.

    The regex covers four secret classes per SPEC.md §4 NFR-04:

        - ``sk-...`` API keys (8+ char body)
        - ``token=...`` query-string secrets
        - ``Bearer ...`` Authorization headers
        - ``postgres://`` / ``postgresql://`` connection strings

    ``re.sub`` on a falsy input returns the input unchanged, so this
    function is total on empty strings.

    Citations: SPEC.md §4 NFR-04.
    """
    return _SECRET_RE.sub("[REDACTED]", text)
