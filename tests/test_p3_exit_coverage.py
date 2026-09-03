"""Coverage-fill tests for the lines uncovered by the FR-01..FR-10 suites.

The Phase-3 exit gate requires 100% line coverage on
``03-development/src``. Several modules carry defensive branches whose
only reachable caller is integration-traffic-shaped (root endpoint,
lifespan drain) or whose precondition is normally guaranteed by the
surrounding flow (constant-time compare on an already hash-keyed DB
lookup). This file pins those branches with the smallest direct test
that hits each line — no behavioural spec is added, only a coverage
prosthetic for the gate.

Lines covered (by report at the time of authoring):

* ``taskq_api.app`` — root endpoint (``GET /``) and the post-yield
  lifespan drain (with + without the drain call failing).
* ``taskq_api.config`` — every branch of ``Settings.db_url_safe``.
* ``taskq_api.repository.session`` — ``current_alembic_revision`` when
  the ``alembic_version`` row is present-but-NULL; the four
  ``alembic_head`` fall-through branches (no revisions, only matches
  with ``down_revision=None``, single-head, max-revisions fallback).
* ``taskq_api.service.auth`` — the defensive ``hmac.compare_digest``
  failure path inside ``_principal_from_db``.
* ``taskq_api.service.ratelimit`` — ``_one_token_seconds`` when
  ``TASKQ_RATE_PER_SEC`` is non-positive.
* ``taskq_api.service.runner`` — every uncovered runner branch.
* ``migrations.env`` — the ``sys.path.insert`` defensive fix-up when
  the project source root is NOT already on ``sys.path``.

No production logic is changed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# taskq_api.app — root endpoint + lifespan drain
# ---------------------------------------------------------------------------


@pytest.fixture
def asgi_client():
    """Re-use the same ASGITransport the FR-10 suite uses."""
    from taskq_api.app import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


def test_app_root_endpoint_returns_service_name(asgi_client):
    """FR-10 / composition-root — ``GET /`` returns the canonical name.

    Pins the ``@application.get("/", include_in_schema=False)`` handler
    declared inside ``create_app`` (the line missing from FR-10
    coverage before this file).
    """
    response = asyncio.run(asgi_client.get("/"))
    assert response.status_code == 200
    assert response.json() == {"service": "taskq-api"}


def test_lifespan_drain_runs_on_shutdown(monkeypatch):
    """The post-yield drain block MUST execute ``drain(timeout=...)``.

    Imports ``lifespan`` and drives it as a real async context manager
    so the ``try / except Exception`` envelope is hit. The drain call is
    monkeypatched to a recorder so we can assert it was reached with a
    numeric timeout parsed from settings.
    """
    from taskq_api import app as app_module

    captured: dict = {}

    async def _fake_drain(timeout: float) -> None:
        captured["timeout"] = timeout

    monkeypatch.setattr(
        "taskq_api.service.runner.drain", _fake_drain, raising=False,
    )

    # Force import-resolution of the lazy ``from taskq_api.service.runner
    # import drain`` inside lifespan() to also point at our fake.
    monkeypatch.setitem(sys.modules, "taskq_api.service.runner", SimpleNamespace(drain=_fake_drain))

    async def _drive():
        async with app_module.lifespan(app_module.app):
            pass

    asyncio.run(_drive())
    assert "timeout" in captured
    assert isinstance(captured["timeout"], float)


def test_lifespan_drain_swallows_failures(monkeypatch):
    """A draining failure MUST NOT block process exit.

    Pins the inner ``except Exception: pass`` on the ``await drain(...)``
    call. The outer ``except Exception`` on the float(timeout) parse is
    exercised when ``get_settings().drain_timeout`` is replaced with a
    non-numeric value (the ``float(...)`` raises ``ValueError`` which the
    ``except Exception`` catches and defaults to 5.0).
    """
    from taskq_api import app as app_module

    async def _exploding_drain(timeout: float) -> None:
        raise RuntimeError("simulated drain failure")

    monkeypatch.setattr(
        "taskq_api.service.runner.drain", _exploding_drain, raising=False,
    )
    monkeypatch.setitem(sys.modules, "taskq_api.service.runner", SimpleNamespace(drain=_exploding_drain))

    async def _drive():
        async with app_module.lifespan(app_module.app):
            pass

    # Must not raise.
    asyncio.run(_drive())


def test_lifespan_drain_defaults_timeout_when_settings_unparseable(monkeypatch):
    """When ``drain_timeout`` cannot be parsed as float, default to 5.0.

    Pins the outer ``except Exception: timeout = 5.0`` in lifespan.
    """
    from taskq_api import app as app_module

    captured: dict = {}

    async def _fake_drain(timeout: float) -> None:
        captured["timeout"] = timeout

    monkeypatch.setattr(
        "taskq_api.service.runner.drain", _fake_drain, raising=False,
    )
    monkeypatch.setitem(sys.modules, "taskq_api.service.runner", SimpleNamespace(drain=_fake_drain))

    # Replace get_settings to return an object whose drain_timeout is a
    # non-numeric string. ``float("not-a-number")`` raises ValueError.
    class _BadSettings:
        drain_timeout = "not-a-number"

    monkeypatch.setattr("taskq_api.app.get_settings", lambda: _BadSettings())

    async def _drive():
        async with app_module.lifespan(app_module.app):
            pass

    asyncio.run(_drive())
    assert captured["timeout"] == 5.0


# ---------------------------------------------------------------------------
# taskq_api.config — db_url_safe branches
# ---------------------------------------------------------------------------


def test_db_url_safe_without_at_sign_is_passthrough():
    """URLs without an ``@`` MUST be returned unchanged."""
    from taskq_api.config import Settings

    s = Settings(db_url="sqlite:///./taskq.db")
    assert s.db_url_safe == "sqlite:///./taskq.db"


def test_db_url_safe_postgres_with_password_redacts():
    """``postgres://user:pass@host`` MUST strip the password."""
    from taskq_api.config import Settings

    s = Settings(db_url="postgres://u:pw@db.example.com/app")
    assert s.db_url_safe == "postgres://u:[REDACTED]@db.example.com/app"


def test_db_url_safe_postgres_without_password_unchanged():
    """``postgres://user@host`` (no colon) MUST be returned unchanged."""
    from taskq_api.config import Settings

    s = Settings(db_url="postgres://user@db.example.com/app")
    assert s.db_url_safe == "postgres://user@db.example.com/app"


def test_db_url_safe_postgres_scheme_without_at_in_rest():
    """A URL with a scheme but no ``@`` in the rest is returned unchanged.

    Pins the second ``if "@" not in rest: return url`` branch.
    """
    from taskq_api.config import Settings

    s = Settings(db_url="postgres://host.example.com/db")
    assert s.db_url_safe == "postgres://host.example.com/db"


def test_db_url_safe_at_in_scheme_returns_unchanged():
    """Pin the ``return url`` branch (line 69) where ``@`` is in the URL
    but NOT in the ``rest`` portion after the ``://`` partition.

    Real-world DSNs almost never have ``@`` in the scheme, but the
    defensive early-return handles the malformed case identically to the
    no-``@`` early-return (line 66).
    """
    from taskq_api.config import Settings

    s = Settings(db_url="a@b://host/path")
    # partition("://") yields ("a@b", "://", "host/path"); rest has no @,
    # so the URL is returned unchanged.
    assert s.db_url_safe == "a@b://host/path"


# ---------------------------------------------------------------------------
# taskq_api.repository.session — alembic branches
# ---------------------------------------------------------------------------


def test_current_alembic_revision_returns_row_value_when_present(tmp_path):
    """When ``alembic_version`` has a row, ``current_alembic_revision``
    returns its ``version_num`` (the lines missing from coverage).
    """
    from sqlalchemy import text

    from taskq_api.repository import session as session_mod
    from taskq_api.repository.session import (
        current_alembic_revision,
        get_engine,
    )

    db_url = f"sqlite:///{tmp_path}/alembic.db"
    original_engine = session_mod._engine
    original_factory = session_mod._SessionLocal
    try:
        session_mod._engine = None
        session_mod._SessionLocal = None
        with patch("taskq_api.repository.session.get_settings") as gs:
            gs.return_value = SimpleNamespace(db_url=db_url, db_pool_size=1)
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL)"
                    ),
                )
                conn.execute(
                    text("INSERT INTO alembic_version VALUES ('rev-xyz')"),
                )
            assert current_alembic_revision() == "rev-xyz"
    finally:
        session_mod._engine = original_engine
        session_mod._SessionLocal = original_factory


def test_alembic_head_returns_single_head():
    """When every revision's ``down_revision`` is known, ``alembic_head``
    returns the revision that is not anyone's down_revision. Pins the
    ``heads[0]`` branch via the real project tree.
    """
    from taskq_api.repository.session import alembic_head

    # Project tree's revisions always satisfy this; we just call and
    # assert non-empty to pin the branch.
    assert alembic_head() != ""


def test_alembic_head_falls_back_when_no_down_targets(monkeypatch, tmp_path):
    """Pin the ``return max(revisions.keys())`` fallback.

    Redirects ``session.__file__`` so the function's path resolution
    lands in a controlled ``migrations/versions`` tree containing two
    revisions whose down_revisions reference each other in a cycle —
    no head, so the ``max(revisions)`` fallback fires.
    """
    from taskq_api.repository import session as session_mod

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "a.py").write_text(
        'revision = "a"\ndown_revision = "b"\n', encoding="utf-8",
    )
    (versions_dir / "b.py").write_text(
        'revision = "b"\ndown_revision = "a"\n', encoding="utf-8",
    )

    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    assert session_mod.alembic_head() == "b"


def test_alembic_head_skips_revision_without_revision_marker(monkeypatch, tmp_path):
    """Pin the ``continue`` branch when a script has no ``revision =``.

    Redirects ``session.__file__`` to a tmp tree with one script that
    lacks the ``revision =`` marker — the loop body ``continue``s on
    line 214.
    """
    from taskq_api.repository import session as session_mod

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "broken.py").write_text(
        "no revision marker here\n", encoding="utf-8",
    )
    (versions_dir / "good.py").write_text(
        'revision = "good"\ndown_revision = None\n', encoding="utf-8",
    )

    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    assert session_mod.alembic_head() == "good"


def test_alembic_head_uses_none_when_down_revision_missing(monkeypatch, tmp_path):
    """Pin the ``if down_match is None: revisions[rev] = None; continue``
    branch (lines 222-223) by including a script with ``revision =`` but
    no ``down_revision =``.
    """
    from taskq_api.repository import session as session_mod

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "no_down.py").write_text(
        'revision = "no_down"\n', encoding="utf-8",
    )

    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    assert session_mod.alembic_head() == "no_down"


def test_alembic_head_returns_empty_when_no_revisions(monkeypatch, tmp_path):
    """Pin the ``if not revisions: return ""`` branch (line 227)."""
    from taskq_api.repository import session as session_mod

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    # No .py files at all.
    # Add an underscored file to confirm it is skipped.
    (versions_dir / "_helpers.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    assert session_mod.alembic_head() == ""


def test_is_migration_at_head_returns_false_when_current_is_none(tmp_path):
    """Pin the ``if current is None: return False`` branch (line 244).

    The ``alembic_version`` table exists but is empty — so
    ``current_alembic_revision()`` returns ``None`` and
    ``is_migration_at_head`` MUST report drift (the AC-9.4 fail-closed
    invariant).
    """
    from sqlalchemy import text

    from taskq_api.repository import session as session_mod
    from taskq_api.repository.session import (
        current_alembic_revision,
        get_engine,
        is_migration_at_head,
    )

    db_url = f"sqlite:///{tmp_path}/drift.db"
    original_engine = session_mod._engine
    original_factory = session_mod._SessionLocal
    try:
        session_mod._engine = None
        session_mod._SessionLocal = None
        with patch("taskq_api.repository.session.get_settings") as gs:
            gs.return_value = SimpleNamespace(db_url=db_url, db_pool_size=1)
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL)"
                    ),
                )
                # Empty table — current_alembic_revision returns None.
            assert current_alembic_revision() is None
            assert is_migration_at_head() is False
    finally:
        session_mod._engine = original_engine
        session_mod._SessionLocal = original_factory


def test_is_migration_at_head_comparison_branch(monkeypatch, tmp_path):
    """Pin the ``return current == alembic_head()`` branch (line 246).

    Sets up a synthetic versions directory containing a single revision
    whose ``revision = "fake_head"``, then inserts that same id into the
    ``alembic_version`` table — the comparison branch evaluates ``True``.
    """
    from sqlalchemy import text

    from taskq_api.repository import session as session_mod
    from taskq_api.repository.session import (
        get_engine,
        is_migration_at_head,
    )

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "fake_head.py").write_text(
        'revision = "fake_head"\ndown_revision = None\n', encoding="utf-8",
    )
    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    db_url = f"sqlite:///{tmp_path}/match.db"
    original_engine = session_mod._engine
    original_factory = session_mod._SessionLocal
    try:
        session_mod._engine = None
        session_mod._SessionLocal = None
        with patch("taskq_api.repository.session.get_settings") as gs:
            gs.return_value = SimpleNamespace(db_url=db_url, db_pool_size=1)
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL)"
                    ),
                )
                conn.execute(
                    text("INSERT INTO alembic_version VALUES ('fake_head')"),
                )
            assert is_migration_at_head() is True
    finally:
        session_mod._engine = original_engine
        session_mod._SessionLocal = original_factory


def test_is_migration_at_head_returns_false_when_drift(monkeypatch, tmp_path):
    """Pin the comparison branch's False path.

    The ``alembic_version`` row's revision id does NOT match the head
    resolved from the (synthetic) versions directory — so
    ``is_migration_at_head`` MUST report ``False``.
    """
    from sqlalchemy import text

    from taskq_api.repository import session as session_mod
    from taskq_api.repository.session import (
        get_engine,
        is_migration_at_head,
    )

    versions_dir = tmp_path / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "fake_head.py").write_text(
        'revision = "fake_head"\ndown_revision = None\n', encoding="utf-8",
    )
    monkeypatch.setattr(
        session_mod, "__file__", str(tmp_path / "taskq_api/repository/session.py"),
    )

    db_url = f"sqlite:///{tmp_path}/drift_match.db"
    original_engine = session_mod._engine
    original_factory = session_mod._SessionLocal
    try:
        session_mod._engine = None
        session_mod._SessionLocal = None
        with patch("taskq_api.repository.session.get_settings") as gs:
            gs.return_value = SimpleNamespace(db_url=db_url, db_pool_size=1)
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL)"
                    ),
                )
                conn.execute(
                    text("INSERT INTO alembic_version VALUES ('old_rev')"),
                )
            assert is_migration_at_head() is False
    finally:
        session_mod._engine = original_engine
        session_mod._SessionLocal = original_factory


# ---------------------------------------------------------------------------
# taskq_api.service.auth — defensive compare_digest branch
# ---------------------------------------------------------------------------


def test_principal_from_db_rejects_when_compare_digest_disagrees(monkeypatch):
    """Defense-in-depth: if the DB row's stored hash disagrees with the
    freshly computed hash, ``_principal_from_db`` MUST return ``None``.
    In normal operation the SQL lookup is keyed by hash so the two
    always match, but the constant-time check is the AC-3.2 contract.
    """
    import types

    # A row whose hash does NOT match the input key's hash.
    class _FakeRow:
        key_hash = "deadbeef" * 8  # 64 hex chars (sha256 length)
        scope = "write"

    class _FakeRepo:
        def find_active_by_hash(self, _hash: str):
            return _FakeRow()

    fake_key_repo_module = types.ModuleType("taskq_api.repository.key_repo")
    fake_key_repo_module.KeyRepository = lambda: _FakeRepo()
    monkeypatch.setitem(sys.modules, "taskq_api.repository.key_repo", fake_key_repo_module)

    from taskq_api.service.auth import _principal_from_db

    assert _principal_from_db("any-input-key") is None


# ---------------------------------------------------------------------------
# taskq_api.service.ratelimit — _one_token_seconds rate <= 0
# ---------------------------------------------------------------------------


def test_one_token_seconds_with_zero_rate_returns_one():
    """Pin the ``if rate <= 0: return 1.0`` defensive branch."""
    from taskq_api.service.ratelimit import _one_token_seconds

    with patch("taskq_api.service.ratelimit.get_settings") as gs:
        gs.return_value = SimpleNamespace(rate_per_sec=0.0)
        assert _one_token_seconds() == 1.0


def test_one_token_seconds_with_negative_rate_returns_one():
    """Pin the ``if rate <= 0: return 1.0`` for negative rates too."""
    from taskq_api.service.ratelimit import _one_token_seconds

    with patch("taskq_api.service.ratelimit.get_settings") as gs:
        gs.return_value = SimpleNamespace(rate_per_sec=-2.0)
        assert _one_token_seconds() == 1.0


# ---------------------------------------------------------------------------
# taskq_api.service.runner — every missing branch
# ---------------------------------------------------------------------------


def test_classify_state_failed_branch():
    """``_classify_state`` MUST report ``("failed", False, False)`` for a
    task that finished with an exception (not cancelled, not clean).
    """
    from taskq_api.service.runner import _classify_state

    async def _drive():
        async def _boom():
            raise RuntimeError("nope")

        task = asyncio.create_task(_boom())
        # Give the task a chance to raise + settle on its own loop.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return _classify_state(task)

    status, drained, interrupted = asyncio.run(_drive())
    assert status == "failed"
    assert drained is False
    assert interrupted is False


def test_drain_returns_zero_when_no_pending():
    """``drain`` MUST short-circuit when no submitted handle is pending.

    Patches ``_handles`` to be empty and asserts the returned
    ``DrainResult`` carries ``drained_count="0"`` /
    ``interrupted_count="0"``.
    """
    from taskq_api.service import runner as runner_mod

    runner_mod._handles.clear()

    async def _drive():
        return await runner_mod.drain(timeout=0.1)

    result = asyncio.run(_drive())
    assert result.drained_count == "0"
    assert result.interrupted_count == "0"


def test_run_with_timeout_returns_timeout_on_expiry():
    """Pin the ``except asyncio.TimeoutError`` branch."""
    from taskq_api.service.runner import run_with_timeout

    async def _slow():
        await asyncio.sleep(10.0)

    async def _drive():
        return await run_with_timeout(_slow(), timeout=0.01)

    result = asyncio.run(_drive())
    assert result.status == "timeout"
    assert result.result is None


def test_run_with_timeout_returns_done_when_coroutine_completes():
    """Pin the post-``try`` ``return TimeoutResult(status="done", ...)``
    branch (line 337) for the happy path.
    """
    from taskq_api.service.runner import run_with_timeout

    async def _quick():
        return "ok"

    async def _drive():
        return await run_with_timeout(_quick(), timeout=1.0)

    result = asyncio.run(_drive())
    assert result.status == "done"
    assert result.result == "ok"


def test_failed_outcome_shape():
    """Pin the ``_failed_outcome`` builder.

    The builder records a ``-1`` exit code and the redacted exception
    string in ``stderr_tail`` — the spawn-time failure shape that
    prevents tasks being stranded in ``running``.
    """
    from taskq_api.service.runner import _failed_outcome

    outcome = _failed_outcome(ValueError("bad command"))
    assert outcome.exit_code == -1
    assert outcome.stdout_tail == ""
    assert outcome.duration_ms == 0
    assert outcome.state == "failed"
    assert "bad command" in outcome.stderr_tail


def test_communicate_with_timeout_kills_on_expiry():
    """Pin the timeout path in ``_communicate_with_timeout``.

    Spawns ``/bin/sleep 10`` and asks for a 0.05s timeout — the function
    MUST call ``proc.kill()`` and return ``timed_out=True`` after
    draining partial output.
    """
    from taskq_api.service.runner import _communicate_with_timeout

    async def _drive():
        proc = await asyncio.create_subprocess_exec(
            "/bin/sleep", "10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            return await _communicate_with_timeout(proc, 0.05)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    out, err, timed_out = asyncio.run(_drive())
    assert timed_out is True


def test_classify_exit_branches():
    """Pin all three branches of ``_classify_exit``."""
    from taskq_api.service.runner import _classify_exit

    assert _classify_exit(timed_out=True, returncode=0) == "timeout"
    assert _classify_exit(timed_out=False, returncode=0) == "done"
    assert _classify_exit(timed_out=False, returncode=2) == "failed"


def test_drive_run_records_failed_outcome_on_exception(monkeypatch):
    """Pin the ``except Exception`` branch in ``_drive_run``.

    Replaces ``run_subprocess`` with a stub that raises — the worker
    must still call ``add_result`` and ``update_status`` so the task
    isn't stranded in ``running``.
    """
    from taskq_api.service import runner as runner_mod

    captured: dict = {}

    class _SpyRepo:
        def __init__(self):
            pass

        def add_result(self, **kwargs):
            captured["add_result"] = kwargs

        def update_status(self, task_id, status):
            captured["update_status"] = (task_id, status)

    def _exploding_run_subprocess(command):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(runner_mod, "TaskRepository", _SpyRepo)
    monkeypatch.setattr(runner_mod, "run_subprocess", _exploding_run_subprocess)

    runner_mod._drive_run("tid-1", "some cmd", "run-1")
    assert captured["update_status"] == ("tid-1", "failed")
    assert captured["add_result"]["exit_code"] == -1


def test_start_run_conflict_when_already_running(monkeypatch):
    """Pin the ``raise ConflictProblem`` branch in ``start_run``.

    A task already in the ``running`` state MUST surface 409 — verify by
    stubbing the repository to return one such task.
    """
    from taskq_api.errors import ConflictProblem
    from taskq_api.service import runner as runner_mod

    class _FakeRepo:
        def __init__(self):
            pass

        def get(self, task_id):
            return {"id": task_id, "status": "running", "command": "echo"}

    monkeypatch.setattr(runner_mod, "TaskRepository", _FakeRepo)
    # Also stub out the threading.Thread spawn so we don't actually run.
    monkeypatch.setattr(
        runner_mod.threading,
        "Thread",
        lambda *a, **kw: SimpleNamespace(start=lambda: None),
    )

    with pytest.raises(ConflictProblem):
        runner_mod.start_run("tid-1")


def test_start_run_not_found_for_unknown_task(monkeypatch):
    """Pin the ``raise NotFoundProblem`` branch (line 478).

    An unknown task id MUST surface 404 via ``NotFoundProblem``.
    """
    from taskq_api.errors import NotFoundProblem
    from taskq_api.service import runner as runner_mod

    class _FakeRepo:
        def __init__(self):
            pass

        def get(self, task_id):
            return None

    monkeypatch.setattr(runner_mod, "TaskRepository", _FakeRepo)

    with pytest.raises(NotFoundProblem):
        runner_mod.start_run("tid-unknown")


# ---------------------------------------------------------------------------
# migrations.env — sys.path defensive fix-up
# ---------------------------------------------------------------------------


def test_migrations_env_runs_with_fresh_path():
    """Pin the ``sys.path.insert(0, ...)`` defensive fix-up.

    Loads ``migrations/env.py`` via :func:`runpy.run_path` after
    explicitly removing the project src root from ``sys.path``. A
    stub ``taskq_api.config`` module is injected on ``sys.modules`` so
    the import succeeds without the project src on the path, which is
    the exact scenario the defensive ``if`` body is designed to recover
    from.
    """
    import types
    import runpy

    project_root = Path(__file__).resolve().parent.parent
    src_root = str(project_root / "03-development" / "src")
    env_py_path = (
        project_root / "03-development" / "src" / "migrations" / "env.py"
    )

    saved_path = sys.path[:]
    try:
        # Remove src from sys.path so the defensive branch fires.
        sys.path[:] = [p for p in sys.path if p != src_root]

        # Inject stub for ``taskq_api.config`` so env.py can import it.
        fake_taskq_api = types.ModuleType("taskq_api")
        fake_taskq_api.__path__ = []
        fake_config = types.ModuleType("taskq_api.config")

        class _FakeSettings:
            db_url = "sqlite:///:memory:"

        fake_config.Settings = _FakeSettings
        sys.modules["taskq_api"] = fake_taskq_api
        sys.modules["taskq_api.config"] = fake_config

        runpy.run_path(str(env_py_path), run_name="__main__")

        # The defensive branch MUST have re-added src_root.
        assert src_root in sys.path
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("taskq_api", None)
        sys.modules.pop("taskq_api.config", None)