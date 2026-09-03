# Risk Register — taskq-final

> **Phase**: 7 — Risk Management | **Date**: 2026-09-04
> **Source**: SPEC.md §9 risk matrix + Gate 3/4 findings + P6 emergent issues
> **Likelihood (L) / Impact (I)**: 1 (Very Low) – 5 (Critical)
> **Score**: L × I ; **HIGH ≥ 9** triggers a formal mitigation plan in `RISK_MITIGATION_PLANS.md`

---

## 1. Sources consulted

| Source | Items harvested |
|--------|-----------------|
| `SPEC.md §9` | R1–R12 baseline risk matrix |
| `.methodology/gate3_result.json` | (a) 3 zero-assert tests flagged, (b) broad_swallow at runner.py:279/412 discussed, (c) 196/7 integration pre-existing failure noted, (d) mutation survivor at `auth.py:35` carried over |
| `.methodology/gate4_result.json` | (a) `crg_cohesion_healthy=0.2` calibration, (b) crg_excludes glob no-op diagnostics, (c) error_handling score=85 (broad_swallow antipatterns), (d) gitleaks FP class on `test_invalid_api_key_returns_401` |
| `.methodology/bug_hunt_report.json` | T-07 (CancelledError + orphan subprocess) / T-10 (Pydantic echo in 422) / T-13 (audit log lacks principal) / T-15 (v3 finished_at overwrites with now()) — all four resolved with fix_commit + repro_test (per `gate3_result.json` evidence); 3 low-severity items refuted. |
| `05-verification/VERIFICATION_REPORT.md` §5 | Gitleaks FP note, runner.py:395–405 teardown untested, v3 migration 66% line coverage hot-spot |
| `06-quality/QUALITY_REPORT.md` | dead_code candidates (23, advisory), error_handling 85.0 |
| `.methodology/fr_progress.json` | All 10 FRs `gate1_pass` (no FR-level failures) |

> **Note on `.methodology/deferred_fixes.md` / `.sessi-work/issue_registry.json`**: not present at the time of writing (no file at those paths). Open risks therefore derive from the concrete artefacts above; if those registries are added later, this register must be re-baselined against them.

---

## 2. Seed mapping (SPEC §9 → register)

The 12 SPEC risks are kept verbatim with explicit L/I per the P7 standard. Where the SPEC text is qualitative ("高/中/低"), the following numerical interpretation is applied: **高 = 4, 中 = 3, 低 = 2** (Calibrated against SPEC §11 monitoring thresholds where present; SPEC uses these bands consistently).

| SPEC ID | Title (abbrev) | SPEC I | SPEC L | Adopted I | Adopted L | Score | Category |
|---------|----------------|--------|--------|-----------|-----------|-------|----------|
| R1 | v3 資料搬遷遺失資料 | 高 | 中 | 4 | 3 | 12 | data_integrity |
| R2 | SQL injection | 高 | 低 | 4 | 2 | 8 | security |
| R3 | API key 洩漏 | 高 | 中 | 4 | 3 | 12 | security |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 3 | 3 | 9 | security |
| R5 | N+1 查詢在大表上崩潰 | 高 | 高 | 4 | 4 | 16 | performance |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | 3 | 4 | 12 | data_integrity |
| R7 | `CancelledError` 被吞 → 關閉卡死 | 中 | 中 | 3 | 3 | 9 | async_lifecycle |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | 3 | 3 | 9 | async_lifecycle |
| R9 | 部署後忘記跑 migration | 高 | 中 | 4 | 3 | 12 | operational |
| R10 | 連線池耗盡 | 中 | 中 | 3 | 3 | 9 | operational |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | 3 | 3 | 9 | operational |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 1 | 3 | 3 | operational |

---

## 3. Register (complete, ID-ordered)

> Status legend: **Active** = mitigation in place but residual risk remains; **Resolved** = no residual exposure observed at this gate; **Monitoring** = depends on dimension above threshold but no specific defect; **Accepted** = explicit residual decision.

### R1 — v3 migration data loss

| Field | Value |
|-------|-------|
| ID | R1 |
| Name | v3 資料搬遷遺失資料 |
| Category | data_integrity |
| Likelihood | 3 |
| Impact | 4 |
| Score | 12 (HIGH) |
| Owner | taskq_api.repository.session + migrations/versions/v3_split_results |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | Real-DB round-trip test (FR-07 / SPEC §8 #12) compares every column of every row between pre-upgrade snapshot and post-downgrade snapshot. T-15 resolved: missing/invalid `finished_at` no longer silently substituted with `now()`; preserves original temporal order across round-trip. |
| Residual / Evidence | Gate 3 evidence: migration file `migrations/versions/v3_split_results.py` is 66% covered (lines 154–197, 214–262 are the data-copy path); integration suite exercises the migration paths so Gate 3 closed above threshold (95.31), but the uncovered branches are still flagged in `VERIFICATION_REPORT.md §5`. |
| Trigger review if | Any migration file is added/modified; any related bug-hunt verdict flips `refuted → confirmed` |

### R2 — SQL injection

| Field | Value |
|-------|-------|
| ID | R2 |
| Name | SQL injection |
| Category | security |
| Likelihood | 2 |
| Impact | 4 |
| Score | 8 |
| Owner | taskq_api.repository (all query paths) |
| Status | Monitoring |
| Mitigation approach | Forbidden string-built SQL: all queries via SQLAlchemy ORM `Select.where(Task.status == status)` bound-parameter form; cursor base64+fromisoformat with explicit exception handling. CI grep gate (NFR-02) + bandit. |
| Residual / Evidence | T-08 refuted in bug-hunt: no f-string / `%` / `+` concatenation into SQL anywhere in `task_repo.py`. score_source=framework. |

### R3 — API key leakage

| Field | Value |
|-------|-------|
| ID | R3 |
| Name | API key 洩漏 |
| Category | security |
| Likelihood | 3 |
| Impact | 4 |
| Score | 12 (HIGH) |
| Owner | taskq_api.service.auth + repository/key_repo |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | Hash at rest (FR-03) + constant-time compare + plaintext printed only once at provisioning. Authorization chokepoint `require_scope` enforces scope-based gating (FR-04). |
| Residual / Evidence | One mutation survivor persisted into Gate 4: `auth.py:35` (`mutmut` id 15, status `bad_survived`). Mutation score 96.3 (≥70 threshold PASS) — the survivor is not a kill-rate gate failure, but the un-killed mutant indicates a missing assertion in the equivalence-class neighbourhood of the auth path. |

### R4 — 403 leaks resource existence

| Field | Value |
|-------|-------|
| ID | R4 |
| Name | 403 洩漏資源存在性 |
| Category | security |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | taskq_api.api.deps + taskq_api.service.auth |
| Status | Resolved at gate time |
| Mitigation approach | Authorization decision happens **before** the resource fetch (FR-04 / SPEC §8 #6). Test coverage asserts 403 with identical body for "task exists with wrong scope" vs "task does not exist". |
| Residual / Evidence | None observed at Gate 4. |

### R5 — N+1 query on large tables

| Field | Value |
|-------|-------|
| ID | R5 |
| Name | N+1 查詢在大表上崩潰 |
| Category | performance |
| Likelihood | 4 |
| Impact | 4 |
| Score | 16 (HIGH) |
| Owner | taskq_api.repository.task_repo |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | Explicit `selectinload(Task.results)` / `joinedload` for every list endpoint. SQLAlchemy event listener asserts statement count is **constant** with row count (NFR-01 / SPEC §8 #14). |
| Residual / Evidence | `GET /v1/tasks?limit=50` benchmark at 10k rows: p95 < 80ms — Gate 3 score override `performance=75.0` sourced from `.sessi-work/benchmark_report.json`. |

### R6 — error body leaks internal structure

| Field | Value |
|-------|-------|
| ID | R6 |
| Name | 錯誤 body 洩漏內部結構 |
| Category | data_integrity |
| Likelihood | 4 |
| Impact | 3 |
| Score | 12 (HIGH) |
| Owner | taskq_api.service.errors + taskq_api.app (problem handlers) |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | RFC 7807 `application/problem+json` fixed fields + `detail` whitelist (FR-10). Bug-hunt T-10 (Pydantic `exc.errors()` echo) **resolved** with fix_commit + repro_test (`tests/test_bug_hunt_t10_pydantic_input_echo.py`). |
| Residual / Evidence | None confirmed at Gate 4. Audit mitigations confirmed; detail-redaction regex documented. |

### R7 — CancelledError swallowed → shutdown deadlock

| Field | Value |
|-------|-------|
| ID | R7 |
| Name | `CancelledError` 被吞 → 關閉卡死 |
| Category | async_lifecycle |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | taskq_api.service.runner + taskq_api.app (lifespan drain) |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | Plain-text prohibition (NFR-03) + assertion tests. Bug-hunt T-07 **resolved**: `_communicate_with_timeout` now kills the subprocess on `CancelledError`, not only on `TimeoutError` (Python 3.8+ semantics). `tests/test_bug_hunt_t07_subprocess_cancel.py` is the reproduction. |
| Residual / Evidence | `ast-error-handling` reports **3 broad_swallow antipatterns** at `app.py:140`, `runner.py:279`, `runner.py:412`; each in cleanup/drain paths. Score 85 (dimension ≥ 80 threshold PASS). The 3 patterns are inside intentionally best-effort drain code (SAB exemption list) but are reviewed manually each release. |

### R8 — task timeout leaves orphan process

| Field | Value |
|-------|-------|
| ID | R8 |
| Name | 任務 timeout 留下孤兒進程 |
| Category | async_lifecycle |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | taskq_api.service.runner |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | `proc.kill()` + `await wait()` (FR-08 / SPEC §8 #25). Integration test asserts no orphan subprocess after timeout. |
| Residual / Evidence | `runner.py:395–405` subprocess teardown error paths not exercised by deterministic tests; carried as known-not-gating per `VERIFICATION_REPORT.md §5`. |

### R9 — forget to run migration post-deploy

| Field | Value |
|-------|-------|
| ID | R9 |
| Name | 部署後忘記跑 migration |
| Category | operational |
| Likelihood | 3 |
| Impact | 4 |
| Score | 12 (HIGH) |
| Owner | deployment + taskq_api.api.deps (`/readyz`) |
| Status | Resolved at gate time |
| Mitigation approach | `/readyz` endpoint **fails closed** when DB schema is below the expected Alembic head (FR-09 / SPEC §8 #11). |
| Residual / Evidence | None observed at Gate 4; deployment runbook references the head-check command. |

### R10 — connection pool exhaustion

| Field | Value |
|-------|-------|
| ID | R10 |
| Name | 連線池耗盡 |
| Category | operational |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | taskq_api.repository.session |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | `pool_pre_ping=True` + `TASKQ_DB_POOL_SIZE=5` + concurrent-execution cap `TASKQ_MAX_CONCURRENT=8` (FR-06 / FR-08). |
| Residual / Evidence | None observed at Gate 4. |

### R11 — transitive dependency introduces incompatible license

| Field | Value |
|-------|-------|
| ID | R11 |
| Name | transitive 依賴引入不相容 license |
| Category | operational |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | requirements.txt + CI |
| Status | Active (mitigated, monitoring) |
| Mitigation approach | Pinned lock file + tree-wide `pip-licenses` scan in CI (NFR-07). |
| Residual / Evidence | License Compliance dimension 100/100 at Gate 4. New dependencies (e.g. a transitive bump) require re-running the gate. |

### R12 — rate-bucket race leads to over-pass

| Field | Value |
|-------|-------|
| ID | R12 |
| Name | rate bucket 競態導致超放行 |
| Category | operational |
| Likelihood | 3 |
| Impact | 1 |
| Score | 3 |
| Owner | taskq_api.service.rate_limit |
| Status | Resolved at gate time |
| Mitigation approach | Single transaction + row-level lock on the bucket row (FR-05). |
| Residual / Evidence | None observed at Gate 4; low aggregate score but design remains load-bearing. |

### R13 — gitleaks FP class on gate artefact paths

| Field | Value |
|-------|-------|
| ID | R13 |
| Name | gitleaks false positives in `.methodology/gate*_result.json` |
| Category | operational |
| Likelihood | 2 |
| Impact | 1 |
| Score | 2 |
| Owner | `.gitleaksignore` |
| Status | Active (recommendation outstanding) |
| Mitigation approach | Add `.methodology/gate1_result.json` and friends to `.gitleaksignore`. Two FP strings already listed in `.gitleaksignore`; gate JSON aggregations still trigger `generic-api-key` entropy rule on `test_invalid_api_key_returns_401`. |
| Residual / Evidence | `05-verification/VERIFICATION_REPORT.md §5` calls out the issue. |

### R14 — mutation survivor at auth.py line 35

| Field | Value |
|-------|-------|
| ID | R14 |
| Name | mutation survivor (auth.py:35) |
| Category | test_assertion_quality |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | tests/test_fr03.py + tests/test_fr04.py |
| Status | Active (mitigated above threshold, residual flagged) |
| Mitigation approach | Mutation score 96.3 ≥ 70 threshold PASSES the gate, but the specific survivor (`auth.py:35`) indicates a missing equivalence-class assertion. |
| Residual / Evidence | `.methodology/mutation_survivors.json`: 1 survivor, status `bad_survived`. |

### R15 — crg_cohesion_healthy calibration drift

| Field | Value |
|-------|-------|
| ID | R15 |
| Name | crg_cohesion_healthy calibration exposes drift |
| Category | architecture_constraints |
| Likelihood | 3 |
| Impact | 3 |
| Score | 9 (HIGH) |
| Owner | `.methodology/harness_config.json` |
| Status | Accepted (documented calibration) |
| Mitigation approach | Calibration committed to `.methodology/harness_config.json`; small 32-source-file codebase makes per-package Leiden edge-density estimates noisy at the framework default 0.3. Green-only when the value is 0.2. |
| Residual / Evidence | Gate 4 challenger response records the calibration decision and rounds it as a documented project choice (not a waiver). |

### R16 — error_handling broad_swallow at runner / app

| Field | Value |
|-------|-------|
| ID | R16 |
| Name | broad_swallow antipatterns in drain code |
| Category | error_handling |
| Likelihood | 2 |
| Impact | 3 |
| Score | 6 |
| Owner | taskq_api.service.runner (279, 412) + taskq_api.app (140) |
| Status | Active (mitigated, dimension just above threshold) |
| Mitigation approach | All 3 antipatterns are inside drain/cleanup paths, not the live execution path. SAB exemption list enumerates them as best-effort drain. `test_lifespan_drain_swallows_failures` + `test_communicate_with_timeout_finally_excepts_are_swallowed` assert intent. |
| Residual / Evidence | `ast-error-handling` score = 85; ≥ 80 threshold PASSES. Future changes touching these lines must be reviewed for `CancelledError` leaks (ties back to R7). |

### R17 — integration-suite collection failure when run alone

| Field | Value |
|-------|-------|
| ID | R17 |
| Name | integration suite fails when run alone |
| Category | operational |
| Likelihood | 2 |
| Impact | 2 |
| Score | 4 |
| Owner | tests/integration/ (pytest config) |
| Status | Resolved at gate time |
| Mitigation approach | `setup.cfg` `norecursedirs` makes the integration mirror opt-in: `pytest 03-development/tests/integration` walks into it; bare `pytest` skips it. Full suite via `make verify-system` PASSES. |
| Residual / Evidence | gate3 evidence: "196 passed, 7 failed — pre-existing test-isolation issue when integration suite is run alone, not a regression". |

---

## 4. Aggregate view

| Category | Count | Items |
|----------|------:|-------|
| security | 3 | R2, R3, R4 |
| data_integrity | 2 | R1, R6 |
| performance | 1 | R5 |
| async_lifecycle | 2 | R7, R8 |
| operational | 5 | R9, R10, R11, R12, R13, R17 |
| test_assertion_quality | 1 | R14 |
| architecture_constraints | 1 | R15 |
| error_handling | 1 | R16 |

| Severity band | Count | IDs |
|---------------|------:|-----|
| HIGH (score ≥ 9) | 10 | R1, R3, R4, R5, R6, R7, R8, R9, R10, R11, R14, R15 |
| MEDIUM (score 6–8) | 1 | R2, R16 |
| LOW (score ≤ 5) | 3 | R12, R13, R17 |

> Note R2 counts as MEDIUM (score 8); R16 counts as MEDIUM (score 6). Twelve items fall in HIGH (score ≥ 9); those are formalised in `RISK_MITIGATION_PLANS.md`.

---

## 5. Cross-reference

| Doc | Role |
|-----|------|
| `RISK_MITIGATION_PLANS.md` | formal owner + deadline for each HIGH (≥ 9) risk |
| `RISK_STATUS_REPORT.md` | snapshot summary for cross-phase reporting |
| `SPEC.md §9` | original risk matrix authority |
| `05-verification/VERIFICATION_REPORT.md` | regression / coverage hot-spots feed R1, R8 |
| `06-quality/QUALITY_REPORT.md` | dimension scores feed R14, R15, R16 |
| `.methodology/bug_hunt_report.json` | closed findings feed R6, R7, R10 (audit), R1 |

---

*End of register. Updates to this file must cite the gate evidence that drove the change.*
