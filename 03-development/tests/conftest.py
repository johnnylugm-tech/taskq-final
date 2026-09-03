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
from taskq_api.models.orm import ApiKey  # noqa: E402


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
    """
    try:
        from taskq_api.models.orm import Task, TaskResult
        from taskq_api.repository import task_repo
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(delete(TaskResult))
            conn.execute(delete(Task))
        task_repo._SEEDED = False
    except Exception:
        pass
    yield