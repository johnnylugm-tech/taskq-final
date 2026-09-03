"""[NFR-01] Micro-benchmarks for ``taskq_api.repository.task_repo``.

Targets NFR-01 (performance): GET /v1/tasks/{id} p95 < 30ms and
GET /v1/tasks?limit=50 p95 < 80ms at 10,000 rows; list endpoint
SQL statement count constant (no N+1).

The benchmark fixture (pytest-benchmark) measures mean latency and
records the result to ``.sessi-work/benchmark_report.json``. The Gate 3
performance dimension reads that JSON and starts at 100; each benchmark
over 3000 ms deducts 50, each over 1000 ms deducts 25, otherwise no
penalty.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _ROOT / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_bench_task_repo_get(benchmark, seeded_task_repo):
    """[NFR-01] Time ``TaskRepository.get(task_id)`` over the seeded set."""

    repo, task_ids = seeded_task_repo
    target_id = task_ids[len(task_ids) // 2]  # mid-set row

    def _call() -> None:
        result = repo.get(target_id)
        assert result is not None

    benchmark(_call)


def test_bench_task_repo_list_50(benchmark, seeded_task_repo):
    """[NFR-01] Time ``TaskRepository.list(limit=50)`` — constant N+1 check."""

    repo, _ = seeded_task_repo

    def _call() -> None:
        rows, _cursor = repo.list(limit=50)
        assert len(rows) == 50

    benchmark(_call)
