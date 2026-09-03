# Architecture Decision Records

## Architecture Amendment — `taskq_api.repository._time` dropped

- **When**: 2026-09-03T02:14:13.372065+00:00
- **Amended**: layer 'repository'
- **Reason**: FR-06 added _time.py as a planned single-source-of-truth for utc_now/as_utc but the refactor that would import from it was never landed; key_repo/rate_repo/task_repo each still define their own _utc_now/_as_utc. Result: zero importers, 0% coverage. Deleting the dead module is the only path; the duplicate helper functions in the three repos are the live code path.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)
