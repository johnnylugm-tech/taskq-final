"""[FR-08, FR-10, NFR-12] FastAPI composition root.

Builds the ``FastAPI`` instance, mounts the routers, and registers the
RFC-7807 exception handlers that every :class:`errors.Problem` maps to.
The lifespan context manager wires the FR-08 graceful drain on shutdown
(AC-8.1).

Citations:
    - SPEC.md §3 FR-08 (drain on shutdown)
    - SPEC.md §3 FR-10 (problem+json error contract)
    - SAD.md §2.9 (composition root), §3.2
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.health import router as health_router
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.config import get_settings
from taskq_api.errors import (
    Problem,
    problem_body,
)


def _problem_response(problem: Problem) -> JSONResponse:
    """[FR-10, FR-05] Render a :class:`Problem` as a ``problem+json`` response.

    A ``retry_after`` on the problem (set by the FR-05 rate limiter) is
    emitted as the ``Retry-After`` header in RFC 9110 §10.2.3 delay-seconds
    form — SPEC.md line 118 requires it on every 429.
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

    application.include_router(health_router)
    application.include_router(tasks_router)
    application.include_router(metrics_router)

    @application.exception_handler(Problem)
    async def _handle_problem(_: Request, exc: Problem) -> JSONResponse:
        return _problem_response(exc)

    @application.exception_handler(RequestValidationError)
    async def _handle_validation(
        _: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        from taskq_api.errors import ValidationProblem, new_correlation_id

        problem = ValidationProblem(detail=str(exc.errors()))
        problem.correlation_id = new_correlation_id()
        return _problem_response(problem)

    @application.get("/", include_in_schema=False)
    async def _root() -> dict:
        return {"service": "taskq-api"}

    return application


# Module-level instance — uvicorn `taskq_api.app:app` looks this up.
app = create_app()
