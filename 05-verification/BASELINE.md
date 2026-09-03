# BASELINE.md — taskq-final

> System state snapshot produced by P5 Verification Author on 2026-09-03.
> Sources: `.methodology/state.json`, `.methodology/quality_manifest.json`,
> `04-testing/TEST_RESULTS.md`, `04-testing/COVERAGE_REPORT.md`,
> `git log`, `tests/bench/test_bench_task_repo.py`.

## 1. Baseline Overview

- Author: P5 Verification Author (orch-post)
- Reviewer: Johnny (project owner)
- session_id: orch-post·P5·per-FR-delta
- Date: 2026-09-03
- Current branch / HEAD: `main` @ `438f845 feat(FR-10): Gate1 PASS — score=97.5 [phase=5]`
- Project version: 0.6.0 (Phase 5 / Per-FR Delta, all 10 FRs Gate 1 PASS, Gate 2 & Gate 3 closed)
- Current methodology phase: 5 — last gate passed: Gate 1 (last gate recorded: 1)
- Last completed phase (handover commit): phase 4 @ sha `b1b73eddfdd524d32e140dd9fb08583d3e488d38` (2026-09-03T07:07:36Z)
- Python toolchain: `/Users/johnny/projects/taskq-final/.venv/bin/python` (CPython 3.11.15, Apple M3 Ultra / arm64, darwin 25.6.0)
- Architecture constraints: `no_circular_dependencies`
- High-risk modules (from quality_manifest.json): `taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.repository.session`, `migrations.versions.v3_split_results`

## 2. Functional Baseline (maps to SRS FR, 100% complete)

| FR ID | Feature Description | Baseline Status | Notes |
|-------|--------------------|-----------------|-------|
| FR-01 | 任務資源 CRUD API | PASS | score 100.0, scope `taskq_api.api.tasks`, `service.tasks`, `service.common`, `repository.task_repo`, `models.schemas`, `models.orm` |
| FR-02 | 任務執行端點 | PASS | score 92.25, scope `taskq_api.api.tasks`, `service.runner`, `repository.task_repo` |
| FR-03 | API Key 認證 | PASS | score 100.0, scope `taskq_api.api.deps`, `service.auth`, `repository.key_repo`, `taskq_api.__main__` |
| FR-04 | Scope 授權 | PASS | score 92.86, scope `taskq_api.api.deps`, `service.auth` |
| FR-05 | 流量控制 | PASS | score 100.0, scope `taskq_api.api.deps`, `service.ratelimit`, `repository.rate_repo` |
| FR-06 | 持久化層與交易邊界 | PASS | score 100.0, scope `taskq_api.repository.session`, `repository.task_repo`, `repository.key_repo`, `repository.rate_repo` |
| FR-07 | Schema Migration (Alembic 三步演進) | PASS | score 99.85, scope `migrations.env`, `versions.v1_initial`, `versions.v2_tags`, `versions.v3_split_results`, `versions._shared` |
| FR-08 | 非同步執行器 | PASS | score 100.0, scope `taskq_api.service.runner`, `app` |
| FR-09 | 健康檢查與可觀測性 | PASS | score 94.5, scope `taskq_api.api.health`, `repository.session` |
| FR-10 | 錯誤契約 (RFC 7807) | PASS | score 97.47, scope `taskq_api.errors`, `app` |

## 3. Quality Baseline

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Gate 3 composite score | ≥ 85 | **95.31** | PASS |
| Gate 2 composite score | ≥ 85 | 93.15 | PASS |
| Test coverage (`03-development/src`) | ≥ 80% | **97%** (1198 stmts / 30 miss) | PASS |
| Mutation score (Gate 1) | ≥ 70 | **70.0** | PASS |
| Logic correctness (Gate 1 average) | ≥ 90 | **97.69** (mean of 10 FR scores) | PASS |
| Per-FR Gate 1 rounds_used (max) | ≤ 5 | 2 across all FRs | PASS |
| `open_critical` / `open_high` (Gate 3) | 0 / 0 | 0 / 0 | PASS |
| Architecture constraints (`no_circular_dependencies`) | pass | pass | PASS |
| Documentation (Gate 3 override) | ≥ 70 | 75.0 | PASS |
| Error handling (Gate 3 override) | ≥ 70 | 80.0 | PASS |
| License compliance (Gate 3 override) | ≥ 70 | 100.0 | PASS |
| Security (Gate 3 override) | ≥ 70 | 80.0 | PASS |
| Integration coverage (Gate 3 override) | ≥ 70 | 80.0 | PASS |
| Readability (Gate 3 override) | ≥ 70 | 80.0 | PASS |
| Test assertion quality (Gate 3 override) | ≥ 70 | 70.0 | PASS |
| Test coverage (Gate 3 override) | ≥ 70 | 80.0 | PASS |
| Execute verification target (Gate 3 override) | ≥ 70 | 100.0 | PASS |

## 4. Performance Baseline (A/B monitoring, NFR-01)

| Metric | Baseline Value | Source |
|--------|---------------|--------|
| `TaskRepository.get(task_id)` p95 | < 30 ms (target NFR-01) | `tests/bench/test_bench_task_repo.py::test_bench_task_repo_get` against 10,000-row seed |
| `TaskRepository.list(limit=50)` p95 | < 80 ms (target NFR-01) | `tests/bench/test_bench_task_repo.py::test_bench_task_repo_list_50` |
| SQL statement count for list endpoint | constant (no N+1) | `selectinload` / `joinedload` usage asserted in `task_repo.py` |
| Benchmark JSON artefact | `.sessi-work/benchmark_report.json` | read by Gate 3 `performance` dimension (score 75.0) |
| Memory (process RSS, in-process bench) | not separately captured at this baseline | tracked qualitatively — no OOM observed in regression run |
| Error rate (test suite) | 0% | 227 passed / 0 failed (`04-testing/TEST_RESULTS.md`) |
| Integration suite (re-run this turn) | 236 passed / 0 failed / 2 warnings | `python -m pytest 03-development/tests/integration -q` (18.68s) |

## 5. Known Issues

| Severity | Count | Description |
|----------|-------|-------------|
| HIGH | 0 | (none — required for baseline sign-off) |
| MEDIUM | 0 | (none) |
| LOW | 3 | `bandit -r 03-development/src/ -ll` flagged 3 LOW-severity findings (`B404 subprocess` import in `service/runner.py`, etc.); informational only, not gating |
| INFO (FP) | 2 | `gitleaks detect` flagged 2 false positives on `.methodology/gate1_result.json:29` and `:90` (test-name string `test_invalid_api_key_returns_401` matched by `generic-api-key` entropy rule); methodology artefact, not source secret |
| INFO (coverage) | 1 | `migrations/versions/v3_split_results.py` 66% covered (data-copy paths lines 154-197, 214-262); exercised in FR-07 integration suite, not gating |
| INFO (coverage) | 1 | `service/runner.py` 95% covered (lines 395-405 subprocess teardown error path); tracked, not gating |

> HIGH severity count must be 0 before establishing baseline. **Verified: 0.**

## 6. Change Log

| Date | Change | Commit / Ref |
|------|--------|--------------|
| 2026-09-03 | feat(FR-10): Gate1 PASS — score=97.5 [phase=5] | `438f845` |
| 2026-09-03 | feat(FR-09): Gate1 PASS — score=94.5 [phase=5] | `9558915` |
| 2026-09-03 | feat(FR-08): Gate1 PASS — score=100.0 [phase=5] | `d3d9045` |
| 2026-09-03 | feat(FR-07): Gate1 PASS — score=99.8 [phase=5] | `e8ba0d7` |
| 2026-09-03 | feat(FR-06): Gate1 PASS — score=100.0 [phase=5] | `f7ed5fd` |
| 2026-09-03 | feat(FR-05): Gate1 PASS — score=100.0 [phase=5] | `ff1b08e` |
| 2026-09-03 | feat(FR-04): Gate1 PASS — score=92.9 [phase=5] | `003757d` |
| 2026-09-03 | feat(FR-03): Gate1 PASS — score=100.0 [phase=5] | `c18d972` |
| 2026-09-03 | feat(FR-02): Gate1 PASS — score=92.2 [phase=5] | `400662f` |
| 2026-09-03 | chore(trace): refresh attestation for b4224a4 (submodule bump) | `55e6875` |

> Module list snapshot (current `03-development/src/`):
> `migrations/{__init__.py, env.py, versions/{__init__.py, _shared/__init__.py, v1_initial.py, v2_tags.py, v3_split_results.py}}`
> `taskq_api/{__init__.py, __main__.py, app.py, config.py, errors.py, api/{__init__.py, deps.py, health.py, metrics.py, tasks.py}, models/{__init__.py, orm.py, schemas.py}, repository/{__init__.py, key_repo.py, rate_repo.py, session.py, task_repo.py, _time.py}, service/{__init__.py, auth.py, common.py, health.py, ratelimit.py, runner.py, tasks.py}}`

## 7. Acceptance Sign-off

- Agent A (Verification Author): P5 orch-post — 2026-09-03
- Approver: Johnny (project owner) — pending sign-off on Phase 5 → Phase 6 advance
- Sign-off criteria met: HIGH severity = 0, Gate 3 composite ≥ 85 (95.31), coverage ≥ 80% (97%), mutation ≥ 70 (70.0), all 10 FRs PASS at Gate 1, integration re-run 236/236, bandit 0 HIGH/MEDIUM, gitleaks 0 real findings (2 documented FPs).
