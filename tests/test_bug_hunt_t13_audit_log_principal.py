"""Adversarial bug-hunt regression test — T-13 (audit log missing principal).

Bug: app.py:_CorrelationIdMiddleware emits an INFO line carrying
correlation_id/method/path but NOT principal.key_id. A privileged action
(admin DELETE /v1/tasks/{id}, GET /v1/metrics) leaves a log line that
cannot be tied back to the caller.

Repro contract (RED): perform an admin DELETE; capture the audit log; assert
the principal.key_id is present in at least one log record.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TASKQ_DB_URL", "sqlite:///./taskq.db")

import pytest  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402

from taskq_api.app import app  # noqa: E402
from taskq_api.service.auth import hash_key  # noqa: E402


@pytest.fixture
def asgi_client():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_admin():
    return {"X-API-Key": "test-admin-key"}


def _run(coro):
    return asyncio.run(coro)


def test_t13_audit_log_captures_principal_for_admin_delete(
    asgi_client, auth_admin, caplog,
):
    """[T-13] The audit log for an admin DELETE must carry principal key_id."""
    # The fixture auth_admin uses 'test-admin-key' → its key_id is the
    # first 16 hex chars of sha256('test-admin-key'). Compute it so we can
    # assert its presence regardless of how the logger formats the line.
    expected_key_id = hash_key("test-admin-key")[:16]

    # Create + delete a task so the wire path exercises a privileged route.
    create_resp = _run(asgi_client.post(
        "/v1/tasks",
        json={"name": "t13-regression-task", "command": "echo hi"},
        headers=auth_admin,
    ))
    assert create_resp.status_code == 201, (
        f"create precondition failed: {create_resp.status_code} {create_resp.text}"
    )
    task_id = create_resp.json()["id"]

    with caplog.at_level(logging.INFO, logger="audit"):
        del_resp = _run(asgi_client.delete(
            f"/v1/tasks/{task_id}", headers=auth_admin,
        ))

    assert del_resp.status_code == 204, (
        f"delete precondition failed: {del_resp.status_code} {del_resp.text}"
    )

    # T-13 contract: an operator must be able to grep the audit log and
    # recover WHO triggered the privileged action. The principal.key_id
    # MUST appear in at least one record on this code path.
    log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
    assert expected_key_id in log_blob, (
        "T-13 regression: audit log does not carry principal.key_id for "
        "an admin DELETE. Expected key_id="
        f"{expected_key_id!r} in audit log; got: {log_blob[:800]}"
    )
