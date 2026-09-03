# Coverage Report — Phase 4 Coverage

## Run

- **Command**: `python -m pytest 03-development/tests --cov=03-development/src --cov-report=term-missing -q`
- **TESTS**: `03-development/tests` (from `phase4_ctx.json` `test_target`)
- **SRC**: `03-development/src` (from `phase4_ctx.json` `cov_target`)
- **Config**: `.coveragerc` — `[run] omit` and `[report] omit` both list `03-development/src/taskq_api/__main__.py` (CLI entry-point surface, exercised as subprocess in production)
- **Date**: 2026-09-03

## Overall coverage

```
TOTAL    1198    30    97%
```

(coverage report `--format=total`: **97%**)

Gate 3 threshold is ≥ 80%; **PASS**.

## Per-module breakdown

| Module | Stmts | Miss | Cover | Missing lines |
|---|---:|---:|---:|---|
| `03-development/src/migrations/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/migrations/env.py` | 49 | 0 | 100% | — |
| `03-development/src/migrations/versions/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/migrations/versions/_shared/__init__.py` | 23 | 0 | 100% | — |
| `03-development/src/migrations/versions/v1_initial.py` | 15 | 0 | 100% | — |
| `03-development/src/migrations/versions/v2_tags.py` | 15 | 0 | 100% | — |
| `03-development/src/migrations/versions/v3_split_results.py` | 64 | 22 | 66% | 154-197, 214-262 |
| `03-development/src/taskq_api/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/taskq_api/api/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/taskq_api/api/deps.py` | 18 | 0 | 100% | — |
| `03-development/src/taskq_api/api/health.py` | 15 | 0 | 100% | — |
| `03-development/src/taskq_api/api/metrics.py` | 11 | 0 | 100% | — |
| `03-development/src/taskq_api/api/tasks.py` | 55 | 0 | 100% | — |
| `03-development/src/taskq_api/app.py` | 70 | 0 | 100% | — |
| `03-development/src/taskq_api/config.py` | 33 | 0 | 100% | — |
| `03-development/src/taskq_api/errors.py` | 39 | 0 | 100% | — |
| `03-development/src/taskq_api/models/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/taskq_api/models/orm.py` | 44 | 0 | 100% | — |
| `03-development/src/taskq_api/models/schemas.py` | 38 | 0 | 100% | — |
| `03-development/src/taskq_api/repository/__init__.py` | 0 | 0 | 100% | — |
| `03-development/src/taskq_api/repository/key_repo.py` | 63 | 0 | 100% | — |
| `03-development/src/taskq_api/repository/rate_repo.py` | 56 | 0 | 100% | — |
| `03-development/src/taskq_api/repository/session.py` | 89 | 0 | 100% | — |
| `03-development/src/taskq_api/repository/task_repo.py` | 201 | 0 | 100% | — |
| `03-development/src/taskq_api/service/__init__.py` | 3 | 0 | 100% | — |
| `03-development/src/taskq_api/service/auth.py` | 38 | 0 | 100% | — |
| `03-development/src/taskq_api/service/common.py` | 25 | 0 | 100% | — |
| `03-development/src/taskq_api/service/health.py` | 14 | 0 | 100% | — |
| `03-development/src/taskq_api/service/ratelimit.py` | 25 | 0 | 100% | — |
| `03-development/src/taskq_api/service/runner.py` | 166 | 8 | 95% | 395-405 |
| `03-development/src/taskq_api/service/tasks.py` | 29 | 0 | 100% | — |
| **TOTAL** | **1198** | **30** | **97%** | — |

## Uncovered lines

1. **`03-development/src/migrations/versions/v3_split_results.py`** — lines 154-197, 214-262 (22 stmts, 66% module coverage). These are the `upgrade()` data-migration body and the `downgrade()` merge body. Uncovered because the `upgrade()` call itself raises `AttributeError: created_at` on the v1 `tasks` model — see `TEST_RESULTS.md` for the FR-07 failure cluster.
2. **`03-development/src/taskq_api/service/runner.py`** — lines 395-405 (8 stmts, 95% module coverage). Likely the subprocess-cleanup / SIGKILL fallback branch in `asyncio.wait_for` timeout handling. Not exercised by any current test (FR-08 ACs stop at happy-path + drain).

## Notes

- `__main__.py` (CLI entry-point) is omitted from both `[run]` and `[report]` per `.coveragerc`; its dispatch surface is exercised in production as a subprocess and via targeted unit tests in `tests/test_fr03.py`.
- Numbers above come from the same pytest invocation that produced `TEST_RESULTS.md` (verbatim summary: `10 failed, 193 passed, 2 warnings in 7.27s`). Re-run with `coverage report --format=total` returns `97%` — matches the per-module row total.