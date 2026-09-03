"""Adversarial bug-hunt regression test — T-10 (Pydantic input echo in 422).

Bug: app.py:_handle_validation builds ValidationProblem with detail=str(exc.errors()).
Pydantic v2's errors() include an 'input' key that echoes the rejected value
verbatim. An attacker POSTing a secret value in a wrong field gets the secret
echoed back in the 422 body.

Repro contract (RED): POST a command whose value contains a high-entropy
secret token. The 422 detail must NOT contain that token.

Gate-3 adversarial_review regression — fix committed with fix(app): strip
pydantic input/ctx from validation problem detail.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Path bootstrap identical to tests/conftest.py — keep this file runnable
# both via the project test runner and the integration mirror.
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TASKQ_DB_URL", "sqlite:///./taskq.db")

import pytest  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402

from taskq_api.app import app  # noqa: E402


@pytest.fixture
def asgi_client():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def auth_write():
    return {"X-API-Key": "test-write-key"}


def _run(coro):
    return asyncio.run(coro)


_SECRET = "sk-THISISMOCKEDSECRET-FOR-T10-REGRESSION-9z8x7c6v5b"


def test_t10_validation_problem_does_not_echo_input_value(
    asgi_client, auth_write,
):
    """[T-10] 422 body MUST NOT echo the caller-supplied input.

    Pydantic v2's errors() default to include_input=True which dumps the
    rejected value verbatim into the response detail. The fix strips
    'input' and 'ctx' from every error dict before stringifying.
    """
    payload = {
        # Pydantic max_length=1000 — make it oversize so the error fires.
        # The token must appear in the input AND must NOT appear in the body.
        "name": "t10-regression",
        "command": ("echo " + _SECRET + " ") * 100,  # > 1000 chars
    }

    response = _run(asgi_client.post(
        "/v1/tasks", json=payload, headers=auth_write,
    ))

    assert response.status_code == 422, (
        f"expected 422 (validation), got {response.status_code}"
    )
    body_text = response.text
    assert _SECRET not in body_text, (
        "T-10 regression: 422 body echoes the caller-supplied input — "
        f"secret token leaked. Body: {body_text[:500]}"
    )
