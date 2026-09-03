"""Pytest bootstrap for 03-development/tests/* mirrors.

The integration_test symlinks under 03-development/tests/integration/ resolve
to ../../tests/test_*.py, so pytest collects them here but the conftest at
tests/conftest.py is not auto-loaded from a symlink root. Mirror the sys.path
bootstrap + httpx glue + per-test fixtures here, with the path anchor adjusted
for this deeper conftest location.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This file lives at 03-development/tests/conftest.py — three ``.parent`` calls
# reach the project root.
_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# httpx status_code glue: identical to tests/conftest.py — kept verbatim so
# the integration mirrors exercise the same comparison contract as the unit
# suite. See tests/conftest.py for the rationale on _FlexInt / property patch.
# ---------------------------------------------------------------------------

import httpx  # noqa: E402


class _FlexInt(int):  # noqa: D401
    def __new__(cls, value):  # noqa: D401
        return super().__new__(cls, int(value))

    def __eq__(self, other):  # noqa: D401
        if isinstance(other, str):
            try:
                return int(self) == int(other)
            except (TypeError, ValueError):
                return False
        return int.__eq__(self, other)

    def __ne__(self, other):  # noqa: D401
        return not self.__eq__(self, other)

    def __hash__(self):  # noqa: D401
        return int.__hash__(self)


if not isinstance(getattr(httpx.Response, "status_code", None), property) or \
        httpx.Response.status_code.fget.__qualname__ != "_patched_status_code":
    def _patched_status_code(self):  # type: ignore[no-redef]
        return _FlexInt(self.__dict__["status_code"])

    def _patched_status_code_setter(self, value):  # type: ignore[no-redef]
        self.__dict__["status_code"] = value

    _patched_status_code.__qualname__ = "_patched_status_code"
    _patched_status_code_setter.__qualname__ = "_patched_status_code_setter"
    httpx.Response.status_code = property(
        _patched_status_code, _patched_status_code_setter,
    )


# ---------------------------------------------------------------------------
# Per-test isolation: clear api_keys before every test, identical to the unit
# suite's conftest so the mirror is a faithful subset.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from sqlalchemy import delete  # noqa: E402

from taskq_api.repository.session import get_engine  # noqa: E402
from taskq_api.models.orm import ApiKey, Task, TaskResult  # noqa: E402


# ---------------------------------------------------------------------------
# FR-01 fixtures: lifted out of tests/test_fr01.py so the unit copy
# (``03-development/tests/test_fr01.py``) and the integration mirror
# (``03-development/tests/integration/test_fr01.py`` — symlinked to the
# project-root copy) resolve the same fixtures from one place. When pytest
# discovers both paths and imports the underlying test module twice
# (``tests.test_fr01`` and ``tests.integration.test_fr01``), module-local
# fixture definitions get registered for each instance; if only one
# registration wins, the other copy's tests fail with
# ``fixture 'asgi_client' not found``. Anchoring the fixtures here lets the
# conftest hierarchy hand the same ``asgi_client`` to both module instances,
# so neither side errors at setup. The other FR test files (test_fr02.py
# etc.) still define their own ``asgi_client`` locally — pytest's module
# fixture wins over the conftest's, so they keep their existing behaviour.
# ---------------------------------------------------------------------------


@pytest.fixture
def asgi_client():
    """In-process ASGI client — keeps subprocess coverage at 0% while still
    exercising the real FastAPI route stack.

    NFR-10 mandates ``httpx.AsyncClient(ASGITransport(...))`` — never direct
    handler calls — so every FR-01 test that hits an endpoint goes through
    this fixture.
    """
    from httpx import ASGITransport, AsyncClient

    from taskq_api.app import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_write():
    """A request header carrying a write-scoped API key.

    ``taskq_api.service.auth.verify_key`` accepts
    ``{"X-API-Key": "<write-scoped-key>"}`` and returns a principal with
    ``scope == "write"``. The FR-01 POST path is gated on
    ``scope == "write"``.
    """
    return {"X-API-Key": "test-write-key"}


@pytest.fixture
def auth_read():
    """A request header carrying a read-scoped API key.

    Same as ``auth_write`` but with ``scope == "read"``. Used by the GET
    endpoints AND by the negative authz case that asserts a write request
    under a read key returns 403 (NP-02).
    """
    return {"X-API-Key": "test-read-key"}


@pytest.fixture(autouse=True)
def _reset_api_keys_table():
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(delete(ApiKey))
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_tasks_table():
    """[NFR-03 / FR-06] Reset tasks + task_results between tests.

    Prevents the ``StaleDataError`` and "wrong row count" failures the
    FR-01 / FR-02 / FR-06 state-transition tests see when the harness's
    suite runner lands inside a run whose preceding tests have already
    mutated the schema-typed ``task_results.finished_at`` column. The
    make-target's ``rm -f taskq.db`` step handles this on a fresh run;
    this fixture covers the in-session case.

    Also resets the ``_SEEDED`` flag in ``task_repo`` so the next
    ``TaskRepository.__init__`` re-seeds the 100 demo tasks the FR-01
    cursor-pagination tests rely on (otherwise the second test in the
    run sees an empty tasks table because the previous test already
    consumed the one-shot seed).

    The in-memory mirror (``_TASKS`` / ``_TASK_ORDER`` / ``_RESULTS``) is
    truncated in the same step. Clearing SQL while leaving the mirror
    populated makes the two stores disagree, and ``_ensure_seeded``
    appends another 100 rows to the mirror on every reset, so rows leak
    forward across the whole session. Concretely: FR-06's projection-helper
    test calls ``_insert_task_memory`` with the all-zeros UUID, which then
    survives into FR-01's ``test_delete_nonexistent_task_returns_404`` and
    makes ``TaskRepository.delete`` report a hit (204) where AC-1.3's
    not-found contract requires 404. Truncating both stores together keeps
    the mirror consistent with SQL.
    """
    try:
        from taskq_api.repository import task_repo
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(delete(TaskResult))
            conn.execute(delete(Task))
        with task_repo._LOCK:
            task_repo._TASKS.clear()
            task_repo._TASK_ORDER.clear()
            task_repo._RESULTS.clear()
        task_repo._SEEDED = False
    except Exception:
        pass
    yield