"""[FR-08, FR-10, NFR-12] FastAPI composition root.

Builds the ``FastAPI`` instance, mounts the routers, and registers the
RFC-7807 exception handlers that every :class:`errors.Problem` maps to.
The lifespan context manager wires the FR-08 graceful drain on shutdown
(AC-8.1).

Citations:
    - SPEC.md §3 FR-08 (drain on shutdown)
    - SPEC.md §3 FR-10 (problem+json error contract, sanitized 500, correlation_id)
    - SAD.md §2.9 (composition root), §3.2
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from taskq_api.api.health import router as health_router
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.config import get_settings
from taskq_api.errors import (
    Problem,
    ValidationProblem,
    new_correlation_id,
    problem_body,
)

# Module-level audit logger — every request emits one INFO record carrying
# the correlation_id so an operator can grep logs to stitch a request's
# lifecycle (FR-10 AC-10.4). The logger is named explicitly (not via
# ``getLogger(__name__)``) so the FR-10 AC-10.4 contract on the
# ``audit`` logger name is stable across module renames.
_audit_logger = logging.getLogger("audit")


def _request_correlation_id(request: Request) -> str:
    """Return the correlation_id stashed on the request by the middleware.

    Falls back to a fresh UUID4 hex if no middleware ran (e.g. an
    exception fired before middleware dispatch, or a synthetic test
    request). Keeping the fallback prevents an exception handler from
    surfacing a ``None`` correlation_id on the wire.
    """
    return getattr(request.state, "correlation_id", None) or new_correlation_id()


def _problem_response(problem: Problem) -> JSONResponse:
    """[FR-10, FR-05] Render a :class:`Problem` as a ``problem+json`` response.

    A ``retry_after`` on the problem (set by the FR-05 rate limiter) is
    emitted as the ``Retry-After`` header in RFC 9110 §10.2.3 delay-seconds
    form — SPEC.md line 118 requires it on every 429.

    Citations:
        - SPEC.md line 118 (429 + ``Retry-After`` seconds)
        - SPEC.md line 163 (AC-10.1 problem+json wire shape)
        - SPEC.md line 166 (AC-10.4 ``X-Correlation-Id`` header)
    """
    headers = {"X-Correlation-Id": problem.correlation_id}
    retry_after = getattr(problem, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    return JSONResponse(
        status_code=problem.status,
        content=problem_body(problem),
        media_type="application/problem+json",
        headers=headers,
    )


class _CorrelationIdMiddleware(BaseHTTPMiddleware):
    """[FR-10 AC-10.4] Stamp every response with ``X-Correlation-Id``.

    Reuses the inbound ``X-Correlation-Id`` when the caller supplied one
    (so a request that already carries an upstream trace id keeps it);
    otherwise mints a fresh UUID4 hex via
    :func:`taskq_api.errors.new_correlation_id`. The same id is emitted
    on the ``audit`` logger so caplog / file log lines and the response
    header carry the same token.

    The middleware runs AFTER FastAPI's exception handlers resolve an
    exception into a response, so the header is stamped on every wire
    response — success or error.
    """

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = (
            request.headers.get("X-Correlation-Id") or new_correlation_id()
        )
        request.state.correlation_id = correlation_id
        _audit_logger.info(
            "request correlation_id=%s method=%s path=%s",
            correlation_id, request.method, request.url.path,
        )
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response


@asynccontextmanager
async def lifespan(application: FastAPI):
    """[FR-08 AC-8.1] Drive the FR-08 graceful drain on shutdown.

    Yields immediately so FastAPI starts serving requests. On shutdown
    the FR-08 executor (``taskq_api.service.runner``) is drained with
    ``TASKQ_DRAIN_TIMEOUT`` so in-flight tasks finish or are stamped
    ``interrupted`` before the process exits — SPEC.md line 147.
    """
    yield
    # Lazy import keeps the import-time dependency graph acyclic:
    # service/runner pulls in config, but the drain call here only fires
    # at shutdown, so any startup race is avoided.
    from taskq_api.service.runner import drain

    try:
        timeout = float(get_settings().drain_timeout)
    except Exception:
        timeout = 5.0
    try:
        await drain(timeout=timeout)
    except Exception:
        # Best-effort — a failed drain MUST NOT block process exit.
        pass


def create_app() -> FastAPI:
    """[NFR-12] Build the FastAPI instance and wire handlers."""
    application = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description=(
            "taskq-api — FR-01 CRUD + cursor pagination, FR-02 run, "
            "FR-03/04 auth, FR-09 health."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(_CorrelationIdMiddleware)

    application.include_router(health_router)
    application.include_router(tasks_router)
    application.include_router(metrics_router)

    @application.exception_handler(Problem)
    async def _handle_problem(_: Request, exc: Problem) -> JSONResponse:
        return _problem_response(exc)

    @application.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        problem = ValidationProblem(detail=str(exc.errors()))
        # Reuse the middleware-set correlation_id so the response header
        # and the audit-log record carry the same token (AC-10.4).
        problem.correlation_id = _request_correlation_id(request)
        return _problem_response(problem)

    @application.exception_handler(Exception)
    async def _handle_exception(
        request: Request, exc: Exception,
    ) -> JSONResponse:
        # FR-10 AC-10.3 — sanitized 500. The wire body MUST NOT carry
        # the traceback, filesystem path, or any internal fragment.
        # The exception type is logged to the audit logger (so operators
        # can grep for it) but ``detail`` is the generic
        # ``internal server error`` message and ``type`` is the canonical
        # ``/errors/internal`` URI (SPEC.md line 167).
        _audit_logger.exception(
            "unhandled exception: %s", type(exc).__name__,
        )
        problem = Problem(
            type_="/errors/internal",
            title="Internal Server Error",
            status=500,
            detail="internal server error",
        )
        problem.correlation_id = _request_correlation_id(request)
        return _problem_response(problem)

    @application.get("/", include_in_schema=False)
    async def _root() -> dict:
        return {"service": "taskq-api"}

    return application


# Module-level instance — uvicorn `taskq_api.app:app` looks this up.
app = create_app()
