# Test Results — Phase 4 Coverage

## Run

- **Command**: `python -m pytest 03-development/tests --cov=03-development/src --cov-report=term-missing -q`
- **Scope**: `03-development/tests` (project's delivered suite under `test_target` from `phase4_ctx.json`)
- **Config**: `.coveragerc` omits only `03-development/src/taskq_api/__main__.py` (entry-point surface)
- **Date**: 2026-09-03

## Verbatim pytest summary line

```
10 failed, 193 passed, 2 warnings in 7.27s
```

## Breakdown

| Metric | Value |
|---|---|
| Tests collected | 203 |
| Passed | 193 |
| Failed | 10 |
| Skipped | 0 |
| Warnings | 2 (Hypothesis `norecursedirs` notice; Starlette `python_multipart` deprecation) |

## Failures

All 10 failures cluster in FR-07 (Alembic three-step migration) and one NFR test that delegates to it. Root cause: `03-development/src/migrations/versions/v3_split_results.py:151` calls `sa.select(... tasks.c.result_json, tasks.c.created_at).select_from(tasks)` — but the v1 schema defines `result_json` and **does not** define `created_at` on the `tasks` table. The migration therefore crashes on first import with `AttributeError: created_at` against the v1 model's `ReadOnlyColumnCollection`. This cascades into every test that calls `v3.upgrade()`.

| Test | Reason |
|---|---|
| `test_v3_split_results_upgrade_splits_and_copies` | v3 upgrade raises `AttributeError: created_at` |
| `test_v3_split_results_upgrade_skips_payloadless_rows` | v3 upgrade raises `AttributeError: created_at` |
| `test_v3_split_results_downgrade_merges_back` | round-trip upgrade fails first |
| `test_v3_now_or_default_when_none_returns_now` | helper module imports upgrade path |
| `test_v3_now_or_default_when_string_invalid_returns_now` | same |
| `test_alembic_upgrade_downgrade_base[AC-7.4-upgrade-head-then-downgrade-base]` | upgrade step crashes |
| `test_v3_data_migration_round_trip_preserves_columns[AC-7.5-round-trip-preserves-column-values]` | upgrade step crashes |
| `test_v3_data_migration_round_trip_preserves_columns[AC-7.5-round-trip-preserves-row-count]` | upgrade step crashes |
| `test_fr07_property_v3_roundtrip_preserves_columns` | Hypothesis case hits same `AttributeError` |
| `test_migration_rollback_on_failure` (NFR) | delegates to FR-07 round-trip test |

## Deferred / open issues

1. **FR-07 v3 migration is broken** — the data-copy `SELECT` references a `created_at` column that v1 does not create. Either add `created_at` to the v1 tasks model or remove the reference from the v3 split query. Until then, 9 FR-07 tests and 1 NFR test fail.

## Coverage gate implication

Despite the FR-07 failures, coverage measurement still runs against the rest of the tree and reports **97%** (see `COVERAGE_REPORT.md`). The 10 failures do not block Gate 3's coverage threshold; they are a separate code-quality defect tracked under FR-07.

## Notes

- Run is scoped to `03-development/tests` only (NOT the repo root, which also includes the vendored `harness/` framework tree).
- Test count reconciliation (`cross_artifact.check_test_count_reconciliation`) compares the `193 passed / 10 failed` figures above against the framework's own `run_suite` measurement of the same tree.