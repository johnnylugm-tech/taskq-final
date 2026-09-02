"""[FR-08, FR-10, NFR-12] FastAPI composition root.

Builds the ``FastAPI`` instance, mounts the routers, and registers the
RFC-7807 exception handlers that every :class:`errors.Problem` maps to.

Citations:
    - SPEC.md §3 FR-08 (drain on shutdown)
    - SPEC.md §3 FR-10 (problem+json error contract)
    - SAD.md §2.9 (composition root), §3.2
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.health import router as health_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import (
    Problem,
    problem_body,
)


def _problem_response(problem: Problem) -> JSONResponse:
    """[FR-10] Render a :class:`Problem` as a ``problem+json`` response."""
    headers = {"X-Correlation-Id": problem.correlation_id}
    return JSONResponse(
        status_code=problem.status,
        content=problem_body(problem),
        media_type="application/problem+json",
        headers=headers,
    )


def create_app() -> FastAPI:
    """[NFR-12] Build the FastAPI instance and wire handlers."""
    application = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description=(
            "taskq-api — FR-01 CRUD + cursor pagination, FR-02 run, "
            "FR-03/04 auth, FR-09 health."
        ),
    )

    application.include_router(health_router)
    application.include_router(tasks_router)

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
