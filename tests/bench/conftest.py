"""Fixtures for the bench/ sub-suite (NFR-01 micro-benchmarks).

The parent conftest at ``03-development/tests/conftest.py`` autouses a
``_reset_api_keys_table`` fixture that runs before every test. We inherit
that here (the integration mirror relies on it). The bench tests need a
stable seeded task table for the duration of the benchmark — adding the
seed once per session and disabling the per-test reset is the simplest
honest path.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


import pytest  # noqa: E402

from taskq_api.repository.task_repo import TaskRepository  # noqa: E402


@pytest.fixture(scope="session")
def seeded_task_repo():
    """[NFR-01] Seed 200 tasks once per benchmark session.

    A 200-row seed is large enough to make ``list(limit=50)`` a real
    workload (4 pages) and small enough to seed in well under a second on
    a dev box. The benchmark itself only times the read path.
    """
    repo = TaskRepository()
    task_ids = []
    for i in range(200):
        row = repo.create(
            name=f"bench-{i:04d}",
            command="echo bench",
        )
        task_ids.append(row["id"])
    return repo, task_ids
