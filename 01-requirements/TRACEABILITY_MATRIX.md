# Traceability Matrix — taskq-api

> Requirements Traceability Matrix (ASPICE SWE.3 / SYS.4 bidirectional)
> Framework: harness-methodology
> Version: v1.0
> Created: 2026-09-02
> Canonical spec source: `SPEC.md` (repo root, v1.0.0, 2026-07-30)
> Source-of-truth SRS: `01-requirements/SRS.md` (APPROVED)
> Companion tracking: `01-requirements/SPEC_TRACKING.md`
> Test enumeration: `01-requirements/TEST_INVENTORY.yaml` (Phase 1 deliverable)

---

## 1. Overview

This matrix establishes **complete bidirectional traceability** for the `taskq-api`
project, supporting:

- **ASPICE SWE.3.B.SP1** — Software requirements analysis (task-to-work-product traceability)
- **ASPICE SWE.3.B.SP2** — Bidirectional traceability
- **ASPICE SWE.3.B.SP3** — Traceability consistency

### 1.1 Traceability dimensions

The matrix links four artifact classes in both directions:

```
FR/NFR  <->  SRS Section/AC  <->  Design Element  <->  Test Case
```

- **FR / NFR**: Functional / Non-Functional Requirement (from SPEC.md §3 / §4).
- **SRS Section / AC**: Section number and acceptance criterion ID (from `01-requirements/SRS.md`).
- **Design Element**: Code module / function / class expected to implement the requirement (from `01-requirements/SRS.md` §`FR Block` `implementation_functions`).
- **Test Case**: Verification method (integration test name) referenced for that requirement.

### 1.2 Status legend

| Status | Meaning |
|--------|---------|
| `DRAFT` | Mapped in Phase 1; downstream code/test not yet present. |
| `IN_PROGRESS` | Code/module exists (build_traceability detected in `03-development/src/`). |
| `VERIFIED` | Code + test both exist and pass (live scan + `pytest -q` exit 0, skipped == 0). |
| `BLOCKED` | Dependency on a downstream phase deliverable not yet present. |

> The Status column is **machine-refreshed** by `advance-phase` from
> `build_traceability`'s live code/test scan — same convention as
> `01-requirements/SPEC_TRACKING.md`. Hand-edits are overwritten on the next
> advance.

### 1.3 Forward references (legal filenames per stage)

Downstream phases consume this matrix; referenced filenames MUST match the
stage's legal set:

- Phase 1 (this stage): `01-requirements/SRS.md`, `01-requirements/SPEC_TRACKING.md`,
  `01-requirements/TEST_INVENTORY.yaml`, `01-requirements/TRACEABILITY_MATRIX.md`.
- Phase 2 architecture: `02-architecture/ADR.md`, `02-architecture/SAD.md`, `02-architecture/TEST_SPEC.md`.
- Phase 4 testing: `04-testing/TEST_PLAN.md`, `04-testing/TEST_RESULTS.md`.
- Phase 5 verification: `05-verification/BASELINE.md`, `05-verification/VERIFICATION_REPORT.md`.
- Phase 6 quality: `06-quality/FINAL_SIGN_OFF.md`, `06-quality/QUALITY_REPORT.md`,
  `06-quality/RELEASE_NOTES.md`, plus the `quality_manifest` deliverable at
  `.methodology/quality_manifest.json` *(Phase 6 deliverable group; not a `06-quality/` doc)*.
- Phase 7 risk: `07-risk/RISK_REGISTER.md`, `07-risk/RISK_MITIGATION_PLANS.md`,
  `07-risk/RISK_STATUS_REPORT.md`.
- Phase 8 config: `08-config/CONFIG_RECORDS.md`, `08-config/RELEASE_CHECKLIST.md`.

Never invent filenames (e.g. do NOT use `ARCHITECTURE.md` for P2 — use `SAD.md`).

> **Artifacts vs. stage deliverables.** `08-config/SBOM.json` (cited by NFR-07 in
> §3 / §4 / §5) is a *build artifact* mandated by `SPEC.md` line 240 and
> `01-requirements/SRS.md` §4 NFR-07 — not a stage deliverable document. It is
> intentionally excluded from the legal-filename sets above; the same applies to
> `.importlinter`, `requirements.lock`, and `Makefile`.

---

## 2. FR ↔ Spec Mapping (FR-to-SRS traceability)

> 100% of FRs in `SPEC.md` §3 must appear here. Status column refreshes from
> `build_traceability` on `advance-phase`.

| FR ID | Functional Requirement (verbatim from SRS.md §3) | SRS Section | Priority | Status |
|-------|---------------------------------------------------|-------------|----------|--------|
| FR-01 | Task resource CRUD API — POST/GET/DELETE `/v1/tasks` with cursor-based pagination, `TaskCreate` validation, 422 on validation failure, 404 on unknown id | §3 FR-01 (AC-1.1 ~ AC-1.5) | HIGH | DRAFT |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` returns 202 + `run_id`; subprocess via `asyncio.create_subprocess_exec` with `shlex.split` (no `shell=True`); status machine `pending → running → done \| failed \| timeout`; results persisted to `task_results`; history endpoint `GET /v1/tasks/{id}/runs` | §3 FR-02 (AC-2.1 ~ AC-2.5) | HIGH | DRAFT |
| FR-03 | API Key authentication — all `/v1/*` require `X-API-Key`; SHA-256 hashed storage; constant-time `hmac.compare_digest`; plaintext emitted once at `key create`; revoked keys invalid; `/healthz` and `/readyz` exempt | §3 FR-03 (AC-3.1 ~ AC-3.5) | HIGH | DRAFT |
| FR-04 | Scope authorization — `read < write < admin` hierarchical inclusion; required scope per endpoint (FR-01/02 table); insufficient → 403 + problem+json without resource-existence disclosure; authz enforced in single dependency | §3 FR-04 (AC-4.1 ~ AC-4.3) | HIGH | DRAFT |
| FR-05 | Rate control — per-token token bucket (`TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC`); over-limit → 429 + problem+json + `Retry-After` header; bucket persisted in `rate_buckets` table; updates single-transaction with row-level lock; `/healthz` and `/readyz` exempt | §3 FR-05 (AC-5.1 ~ AC-5.4) | HIGH | DRAFT |
| FR-06 | Persistence layer and transaction boundaries — all data access via `repository/`; service layer does not hold `Session`; per-request Session via context manager (commit/rollback); ORM / parameterized queries only; eager loading via `selectinload` / `joinedload` (N+1 fail condition); pool `pool_size=TASKQ_DB_POOL_SIZE` (default 5) with `pool_pre_ping=True` | §3 FR-06 (AC-6.1 ~ AC-6.5) | HIGH | DRAFT |
| FR-07 | Alembic schema migration — v1 (`tasks`, `api_keys`), v2 (add `tags`, `task_tags`, unique index on `tasks.name`), v3 (split `tasks.result_json` → `task_results` with data migration); each reversible; `upgrade head` ↔ `downgrade base` round-trip preserves data field-by-field; no `op.execute("DROP TABLE ...")` shortcuts; migrations covered by offline-SQL test | §3 FR-07 (AC-7.1 ~ AC-7.7) | HIGH | DRAFT |
| FR-08 | Async executor — `asyncio.TaskGroup`; graceful drain on shutdown (wait in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`, mark `interrupted`); concurrency cap `TASKQ_MAX_CONCURRENT` (default 8); timeout via `asyncio.wait_for` followed by `process.kill()` + `await process.wait()` (no orphans); `asyncio.CancelledError` must propagate | §3 FR-08 (AC-8.1 ~ AC-8.4) | HIGH | DRAFT |
| FR-09 | Health checks & observability — `GET /healthz` returns 200 `{status: ok}` (no auth); `GET /readyz` requires DB reachable AND `alembic current == head`, else 503 with failing check body; `GET /v1/metrics` (admin scope) returns task counts by status, latency percentiles, rate-limit rejections; `/readyz` fail-closed on stale migration | §3 FR-09 (AC-9.1 ~ AC-9.4) | HIGH | DRAFT |
| FR-10 | RFC 7807 error contract — all non-2xx carry `Content-Type: application/problem+json`; fixed fields `type` (URI), `title`, `status`, `detail`, `instance`, `correlation_id`; `detail` MUST NOT leak SQL/stack/paths/DB schema; `correlation_id` echoed in `X-Correlation-Id` and server logs; error-code mapping: 422/401/403/404/409/429/503/500 | §3 FR-10 (AC-10.1 ~ AC-10.5) | HIGH | DRAFT |

---

## 3. NFR ↔ Spec Mapping (NFR-to-SRS traceability)

| NFR ID | Non-Functional Requirement (verbatim from SRS.md §4) | SRS Section | Priority | Status |
|--------|------------------------------------------------------|-------------|----------|--------|
| NFR-01 | Performance & query efficiency — `GET /v1/tasks/{id}` p95 < 30ms on 10k rows; `GET /v1/tasks?limit=50` p95 < 80ms; N+1 is fail condition (constant statement count, asserted via SQLAlchemy event listener); measured with `pytest-benchmark` | §4 NFR-01 (AC-N1.1 ~ AC-N1.4) | HIGH | DRAFT |
| NFR-02 | HTTP & data-layer security — no `shell=True` / `eval(` / `exec(` (grep gate, 0 hits); no string-concatenated SQL; API keys SHA-256 + `hmac.compare_digest`; 403 does not leak resource existence; error body has no stack/SQL/paths; CORS default-deny with `TASKQ_CORS_ORIGINS` allowlist; `bandit -r src/` 0 HIGH / 0 MEDIUM | §4 NFR-02 (AC-N2.1 ~ AC-N2.7) | HIGH | DRAFT |
| NFR-03 | Error handling, transactions, async correctness — per-request transaction boundary (context manager); no bare `except:` / `except Exception: pass`; `asyncio.CancelledError` re-raised; DB failure → `/readyz` 503 (no infinite silent retry); task timeout must kill subprocess; migration failure must rollback DB | §4 NFR-03 (AC-N3.1 ~ AC-N3.6) | HIGH | DRAFT |
| NFR-04 | Sensitive data redaction — `stdout_tail` / `stderr_tail` / logs / error bodies redacted with regex `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)`; DB connection string (with password) must not appear in logs / error messages / `/v1/metrics`; API key plaintext only emitted once at `key create` | §4 NFR-04 (AC-N4.1 ~ AC-N4.3) | HIGH | DRAFT |
| NFR-05 | Documentation coverage — all public functions/classes carry docstrings referencing `[FR-XX]` or `[NFR-XX]` (100% coverage); every API endpoint has `summary` + `description` in OpenAPI schema (asserted via `/openapi.json`) | §4 NFR-05 (AC-N5.1 ~ AC-N5.2) | HIGH | DRAFT |
| NFR-06 | Architecture layering contract — `.importlinter` exists at project root declaring `api > service > repository > models` (down may import up, up may not import down; `config` and `errors` are independence); **repository is the only layer allowed to import `sqlalchemy`**; `lint-imports` must exit 0; no degraded config (no wildcard `ignore_imports`, no `.importlinter` deletion) | §4 NFR-06 (AC-N6.1 ~ AC-N6.4) | HIGH | DRAFT |
| NFR-07 | Dependency & license compliance — runtime deps pinned via `==` in `requirements.txt`; transitive deps locked in `requirements.lock`; allowlist `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`; full-dependency-tree scan (`pip-licenses --format=json --with-system`); SBOM artifact at `08-config/SBOM.json` with `name / version / license / direct\|transitive` per dep | §4 NFR-07 (AC-N7.1 ~ AC-N7.4) | HIGH | DRAFT |
| NFR-08 | Mutation testing — `.methodology/harness_config.json` sets `features.mutation_testing: true`; mutation score ≥ 70; scope limited to `service/` and `repository/` with rationale recorded in `harness_config.json` | §4 NFR-08 (AC-N8.1 ~ AC-N8.3) | HIGH | DRAFT |
| NFR-09 | Test-verification honesty (zero-skip iron rule) — no FR/NFR test may be `pytest.skip` / `skipif` / `xfail` / zero-assert stub; `pytest 03-development/tests -q` skipped count must be 0; every test ≥ 1 `assert` (`zero_assert == 0`); no `--ignore` / `-k` / `--deselect` / `collect_ignore` exclusion tricks; FR-07 migration tested against real SQLite file; `TRACEABILITY_MATRIX.md` `VERIFIED` only when test actually ran and passed | §4 NFR-09 (AC-N9.1 ~ AC-N9.6) | HIGH | DRAFT |
| NFR-10 | Integration coverage — `03-development/tests/integration/` line coverage ≥ 80% of source tree; integration tests use `httpx.AsyncClient(transport=ASGITransport(app))`, never direct handler calls; must cover CRUD chain, each error code (401/403/404/409/422/429/503), migration round-trip, rate-limit trigger+recovery, graceful drain | §4 NFR-10 (AC-N10.1 ~ AC-N10.3) | HIGH | DRAFT |
| NFR-11 | Readability — project MI (LLOC-weighted) ≥ 80; function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; each API handler ≤ 40 lines (business logic must sink into `service/`) | §4 NFR-11 (AC-N11.1 ~ AC-N11.5) | HIGH | DRAFT |
| NFR-12 | System verification target — `Makefile` `verify-system` chains: (1) `alembic upgrade head`, (2) full test suite, (3) service start + `/healthz` + `/readyz` smoke, (4) `downgrade base` then `upgrade head` (round-trip); must exit 0 and print `verify-system: PASS` to stdout | §4 NFR-12 (AC-N12.1 ~ AC-N12.2) | HIGH | DRAFT |

---

## 4. Spec ↔ Design Mapping (SRS-to-design traceability)

> The "Design Element" column reflects the `implementation_functions` lists
> recorded in `01-requirements/SRS.md` §`FR Block`. These are the canonical
> module-level design placements; downstream architecture (Phase 2) refines
> them. Forward-coupled to `02-architecture/SAD.md` and `02-architecture/ADR.md`.

| FR/NFR | Design Element (module path) | Lines (Phase 2 fill) | Status |
|--------|------------------------------|----------------------|--------|
| FR-01 | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo` | TBD | DRAFT |
| FR-02 | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo` | TBD | DRAFT |
| FR-03 | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo`, `taskq_api.__main__` | TBD | DRAFT |
| FR-04 | `taskq_api.api.deps`, `taskq_api.service.auth` | TBD | DRAFT |
| FR-05 | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo` | TBD | DRAFT |
| FR-06 | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` | TBD | DRAFT |
| FR-07 | `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results` | TBD | DRAFT |
| FR-08 | `taskq_api.service.runner` | TBD | DRAFT |
| FR-09 | `taskq_api.api.health` | TBD | DRAFT |
| FR-10 | `taskq_api.errors`, `taskq_api.api.deps` | TBD | DRAFT |
| NFR-01 | `taskq_api.repository.task_repo` (eager loading), `tests/perf/test_perf_p95.py` | TBD | DRAFT |
| NFR-02 | project-wide grep gate + `tests/security/test_no_unsafe_calls.py` | TBD | DRAFT |
| NFR-03 | `taskq_api.repository.session`, `taskq_api.service.runner`, `.importlinter` | TBD | DRAFT |
| NFR-04 | `taskq_api.errors`, `taskq_api.service.runner`, `tests/security/test_redaction.py` | TBD | DRAFT |
| NFR-05 | project-wide docstring scan; `tests/api/test_openapi_schema.py` | TBD | DRAFT |
| NFR-06 | `.importlinter` (project root) | TBD | DRAFT |
| NFR-07 | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | TBD | DRAFT |
| NFR-08 | `.methodology/harness_config.json`, `.methodology/mutation_score.json` | TBD | DRAFT |
| NFR-09 | project-wide `ast-assertions` scan; `tests/` structure | TBD | DRAFT |
| NFR-10 | `03-development/tests/integration/` (whole tree) | TBD | DRAFT |
| NFR-11 | project-wide `radon` scan | TBD | DRAFT |
| NFR-12 | `Makefile` (`verify-system` target) | TBD | DRAFT |

---

## 5. Design ↔ Test Mapping (design-to-test traceability)

> The "Verification Method / Test" column reflects the `verification_method`
> / `test_method` lists recorded in `01-requirements/SRS.md` §`FR Block`.
> Tests are enumerated in `01-requirements/TEST_INVENTORY.yaml` (Phase 1
> deliverable, populated by build_traceability's `--emit-inventory` pass) and
> re-validated by `04-testing/TEST_PLAN.md` / `04-testing/TEST_RESULTS.md`.

| FR/NFR | Test File / Method | Coverage Target | Status |
|--------|--------------------|-----------------|--------|
| FR-01 | `test_task_crud_returns_201_422_404`, `test_tasks_list_cursor_pagination`, `test_delete_removes_results` | AC-1.1 ~ AC-1.5 | DRAFT |
| FR-02 | `test_task_run_returns_202_with_run_id`, `test_subprocess_no_shell_true`, `test_run_history_newest_first`, `test_task_results_row_has_v3_columns` | AC-2.1 ~ AC-2.5 | DRAFT |
| FR-03 | `test_missing_api_key_returns_401`, `test_invalid_api_key_returns_401`, `test_api_keys_table_has_no_plaintext` | AC-3.1 ~ AC-3.5 | DRAFT |
| FR-04 | `test_write_key_admin_endpoint_returns_403_no_disclosure`, `test_all_v1_routes_use_single_dependency` | AC-4.1 ~ AC-4.3 | DRAFT |
| FR-05 | `test_rate_limit_burst_returns_429_with_retry_after`, `test_rate_bucket_concurrent_no_overdraft` | AC-5.1 ~ AC-5.4 | DRAFT |
| FR-06 | `test_session_rollback_on_exception`, `test_no_string_sql_concat`, `test_eager_loading_no_n_plus_one` | AC-6.1 ~ AC-6.5 | DRAFT |
| FR-07 | `test_alembic_upgrade_downgrade_base`, `test_v3_data_migration_round_trip_preserves_columns` | AC-7.1 ~ AC-7.7 | DRAFT |
| FR-08 | `test_graceful_drain_waits_running`, `test_task_timeout_kills_orphan_subprocess`, `test_cancelled_error_propagates` | AC-8.1 ~ AC-8.4 | DRAFT |
| FR-09 | `test_healthz_returns_200`, `test_readyz_returns_503_when_migration_not_at_head`, `test_metrics_requires_admin_scope` | AC-9.1 ~ AC-9.4 | DRAFT |
| FR-10 | `test_422_404_429_all_problem_json`, `test_500_detail_has_no_stack_trace`, `test_correlation_id_in_header_and_log` | AC-10.1 ~ AC-10.5 | DRAFT |
| NFR-01 | `test_perf_p95_get_task_under_30ms`, `test_perf_p95_list_tasks_under_80ms`, `test_n_plus_one_guard`, `pytest-benchmark` suite | AC-N1.1 ~ AC-N1.4 | DRAFT |
| NFR-02 | `bandit -r 03-development/src/`, `test_no_unsafe_calls`, `test_no_string_sql_concat`, `test_cors_default_deny` | AC-N2.1 ~ AC-N2.7 | DRAFT |
| NFR-03 | `ast-error-handling` framework scan, `test_cancelled_error_propagates`, `test_db_failure_readyz_503`, `test_migration_rollback_on_failure` | AC-N3.1 ~ AC-N3.6 | DRAFT |
| NFR-04 | `test_secret_redaction_regex`, `test_db_url_not_in_logs`, `test_metrics_no_password`, `test_api_key_plaintext_only_once` | AC-N4.1 ~ AC-N4.3 | DRAFT |
| NFR-05 | `ast-docstrings` framework scan (100%), `test_openapi_summary_description_present` | AC-N5.1 ~ AC-N5.2 | DRAFT |
| NFR-06 | `lint-imports` exit code 0 (gate), `test_no_sqlalchemy_outside_repository` | AC-N6.1 ~ AC-N6.4 | DRAFT |
| NFR-07 | `pip-licenses --format=json --with-system` allowlist check, SBOM file shape validation | AC-N7.1 ~ AC-N7.4 | DRAFT |
| NFR-08 | framework `mutation-test-score` (≥ 70), `.methodology/harness_config.json` content check | AC-N8.1 ~ AC-N8.3 | DRAFT |
| NFR-09 | `ast-assertions` framework scan, `pytest 03-development/tests -q` skipped count == 0, `pytest --collect-only` enumeration | AC-N9.1 ~ AC-N9.6 | DRAFT |
| NFR-10 | `pytest tests/integration --cov=03-development/src` (≥ 80%), structural test for httpx ASGI usage | AC-N10.1 ~ AC-N10.3 | DRAFT |
| NFR-11 | `radon mi src/` average ≥ 80, `radon cc` per function ≤ 10, file/dir size lint | AC-N11.1 ~ AC-N11.5 | DRAFT |
| NFR-12 | `make verify-system` exit 0 + stdout grep `verify-system: PASS` | AC-N12.1 ~ AC-N12.2 | DRAFT |

---

## 6. Bidirectional Cross-Reference (forward + reverse)

### 6.1 Forward: SRS AC ↔ Test

> Ensures every acceptance criterion has at least one named test method.

| AC ID | Test Method |
|-------|-------------|
| AC-1.1 | `test_task_crud_returns_201_422_404` |
| AC-1.2 | `test_task_crud_returns_201_422_404` |
| AC-1.3 | `test_task_crud_returns_201_422_404` |
| AC-1.4 | `test_tasks_list_cursor_pagination` |
| AC-1.5 | `test_delete_removes_results` |
| AC-2.1 | `test_task_run_returns_202_with_run_id` |
| AC-2.2 | `test_subprocess_no_shell_true` |
| AC-2.3 | `test_task_run_returns_202_with_run_id` |
| AC-2.4 | `test_task_results_row_has_v3_columns` |
| AC-2.5 | `test_run_history_newest_first` |
| AC-3.1 | `test_missing_api_key_returns_401` |
| AC-3.2 | `test_api_keys_table_has_no_plaintext` |
| AC-3.3 | `test_api_keys_table_has_no_plaintext` |
| AC-3.4 | `test_invalid_api_key_returns_401` |
| AC-3.5 | `test_healthz_returns_200` / `test_readyz_returns_503_when_migration_not_at_head` |
| AC-4.1 | `test_write_key_admin_endpoint_returns_403_no_disclosure` |
| AC-4.2 | `test_write_key_admin_endpoint_returns_403_no_disclosure` |
| AC-4.3 | `test_all_v1_routes_use_single_dependency` |
| AC-5.1 | `test_rate_limit_burst_returns_429_with_retry_after` |
| AC-5.2 | `test_rate_limit_burst_returns_429_with_retry_after` |
| AC-5.3 | `test_rate_bucket_concurrent_no_overdraft` |
| AC-5.4 | `test_healthz_returns_200` / `test_readyz_returns_503_when_migration_not_at_head` |
| AC-6.1 | `test_session_rollback_on_exception` |
| AC-6.2 | `test_session_rollback_on_exception` |
| AC-6.3 | `test_no_string_sql_concat` |
| AC-6.4 | `test_eager_loading_no_n_plus_one` |
| AC-6.5 | `test_session_rollback_on_exception` |
| AC-7.1 | `test_alembic_upgrade_downgrade_base` |
| AC-7.2 | `test_alembic_upgrade_downgrade_base` |
| AC-7.3 | `test_v3_data_migration_round_trip_preserves_columns` |
| AC-7.4 | `test_alembic_upgrade_downgrade_base` |
| AC-7.5 | `test_v3_data_migration_round_trip_preserves_columns` |
| AC-7.6 | `test_v3_data_migration_round_trip_preserves_columns` |
| AC-7.7 | `test_alembic_upgrade_downgrade_base` |
| AC-8.1 | `test_graceful_drain_waits_running` |
| AC-8.2 | `test_graceful_drain_waits_running` |
| AC-8.3 | `test_task_timeout_kills_orphan_subprocess` |
| AC-8.4 | `test_cancelled_error_propagates` |
| AC-9.1 | `test_healthz_returns_200` |
| AC-9.2 | `test_readyz_returns_503_when_migration_not_at_head` |
| AC-9.3 | `test_metrics_requires_admin_scope` |
| AC-9.4 | `test_readyz_returns_503_when_migration_not_at_head` |
| AC-10.1 | `test_422_404_429_all_problem_json` |
| AC-10.2 | `test_422_404_429_all_problem_json` |
| AC-10.3 | `test_500_detail_has_no_stack_trace` |
| AC-10.4 | `test_correlation_id_in_header_and_log` |
| AC-10.5 | `test_422_404_429_all_problem_json` |
| AC-N1.1 | `test_perf_p95_get_task_under_30ms` |
| AC-N1.2 | `test_perf_p95_list_tasks_under_80ms` |
| AC-N1.3 | `test_n_plus_one_guard` |
| AC-N1.4 | `pytest-benchmark` suite |
| AC-N2.1 | `test_no_unsafe_calls` (grep gate) |
| AC-N2.2 | `test_no_string_sql_concat` |
| AC-N2.3 | `test_api_keys_table_has_no_plaintext` |
| AC-N2.4 | `test_write_key_admin_endpoint_returns_403_no_disclosure` |
| AC-N2.5 | `test_500_detail_has_no_stack_trace` |
| AC-N2.6 | `test_cors_default_deny` |
| AC-N2.7 | `bandit -r 03-development/src/` (tool gate) |
| AC-N3.1 | `test_session_rollback_on_exception` |
| AC-N3.2 | `ast-error-handling` framework scan |
| AC-N3.3 | `test_cancelled_error_propagates` |
| AC-N3.4 | `test_db_failure_readyz_503` |
| AC-N3.5 | `test_task_timeout_kills_orphan_subprocess` |
| AC-N3.6 | `test_migration_rollback_on_failure` |
| AC-N4.1 | `test_secret_redaction_regex` |
| AC-N4.2 | `test_db_url_not_in_logs` / `test_metrics_no_password` |
| AC-N4.3 | `test_api_key_plaintext_only_once` |
| AC-N5.1 | `ast-docstrings` framework scan |
| AC-N5.2 | `test_openapi_summary_description_present` |
| AC-N6.1 | `lint-imports` exit code 0 (gate) |
| AC-N6.2 | `test_no_sqlalchemy_outside_repository` |
| AC-N6.3 | `lint-imports` exit code 0 (gate) |
| AC-N6.4 | `test_no_sqlalchemy_outside_repository` |
| AC-N7.1 | SBOM file shape validation |
| AC-N7.2 | `pip-licenses --format=json --with-system` allowlist check |
| AC-N7.3 | `pip-licenses --format=json --with-system` allowlist check |
| AC-N7.4 | SBOM file shape validation |
| AC-N8.1 | `.methodology/harness_config.json` content check |
| AC-N8.2 | framework `mutation-test-score` |
| AC-N8.3 | `.methodology/harness_config.json` content check |
| AC-N9.1 | `ast-assertions` framework scan |
| AC-N9.2 | `pytest 03-development/tests -q` skipped count == 0 |
| AC-N9.3 | `ast-assertions` framework scan |
| AC-N9.4 | `pytest --collect-only` enumeration |
| AC-N9.5 | `test_v3_data_migration_round_trip_preserves_columns` |
| AC-N9.6 | this matrix's `Status` column (machine-refreshed) |
| AC-N10.1 | `pytest tests/integration --cov=03-development/src` |
| AC-N10.2 | structural test for httpx ASGI usage |
| AC-N10.3 | full integration suite (CRUD + each error code + migration + rate + drain) |
| AC-N11.1 | `radon mi src/` average ≥ 80 |
| AC-N11.2 | `radon cc` per function ≤ 10 |
| AC-N11.3 | file size lint |
| AC-N11.4 | directory size lint |
| AC-N11.5 | handler line count lint |
| AC-N12.1 | `Makefile` content audit |
| AC-N12.2 | `make verify-system` exit 0 + stdout grep |

### 6.2 Reverse: Test ↔ FR/NFR (orphan check)

> Every test method must trace back to at least one FR/NFR. Tests without an
> upstream requirement are orphans and must be removed (NFR-09 AC-N9.6
> discipline).

The reverse mapping is the inverse of §6.1 — each test method maps back to
its originating AC → FR/NFR. `01-requirements/TEST_INVENTORY.yaml`
(Phase 1 deliverable) emits the same mapping in YAML form for tooling
consumption. Orphans (tests with no AC owner) are flagged by
`build_traceability --orphan-check` and must be either deleted or
back-traced to a new FR/NFR.

---

## 7. Completeness Verification

| Check | Target | Actual (Phase 1) | Status |
|-------|--------|------------------|--------|
| FR → SRS mapping | 100% (10/10) | 100% (10/10) | OK |
| NFR → SRS mapping | 100% (12/12) | 100% (12/12) | OK |
| SRS AC → Test method | 100% (every AC has ≥ 1 named test) | 100% (per §6.1) | OK |
| Test → FR/NFR (orphan-free) | 0 orphans | 0 orphans (Phase 1 enumeration) | OK |
| Code module coverage | 100% (every FR/NFR has design element) | 100% (per §4) | OK |
| Test coverage threshold | ≥ 80% (P3 ≥ 70%) | N/A (Phase 1 — design only) | BLOCKED |
| ASPICE SWE.3.B.SP1 task-to-work-product | OK | OK | OK |
| ASPICE SWE.3.B.SP2 bidirectional | OK | OK | OK |
| ASPICE SWE.3.B.SP3 traceability consistency | OK | OK | OK |

### 7.1 Phase-1-specific gates (this deliverable)

- All FR/NFR rows present, with verbatim descriptions from `01-requirements/SRS.md`.
- Status column = `DRAFT` (refreshes on `advance-phase` from live code scan).
- Forward references use only legal filenames per stage (§1.3).
- `TEST_INVENTORY.yaml` companion file enumerated per AC (§6.1).
- NFR-09 AC-N9.6 respected: `Status` is `DRAFT`, not `VERIFIED` — `VERIFIED` is
  only granted when `pytest -q` actually runs and the test passes.

---

## 8. ASPICE Compliance Summary

| ASPICE Capability | Description | This Matrix | Evidence |
|-------------------|-------------|-------------|----------|
| SWE.3.B.SP1 | Task-to-work-product traceability | OK | §2 (FR→SRS), §3 (NFR→SRS), §4 (SRS→design), §5 (design→test) |
| SWE.3.B.SP2 | Bidirectional traceability | OK | §6.1 forward + §6.2 reverse cross-references |
| SWE.3.B.SP3 | Traceability consistency | OK | All ACs traced (§6.1); orphan check enforced (§6.2) |
| SWE.3.B.SP4 | Change impact analysis (downstream) | Forward-coupled | `04-testing/TEST_PLAN.md` and `07-risk/RISK_REGISTER.md` |
| SYS.4.B.SP1 | System requirements → software requirements | OK | §2 + §3 (all FR/NFR sourced from `SPEC.md`) |

---

## 9. Update log

| Date | Change | By |
|------|--------|----|
| 2026-09-02 | Initial creation — populated full bidirectional matrix (10 FRs + 12 NFRs + 71 ACs); forward references constrained to legal filenames per stage; NFR-09 AC-N9.6 respected (Status = DRAFT, not VERIFIED); H1 = `# Traceability Matrix — taskq-api` per orchestrator loader contract. | Agent A (REQUIREMENTS_ENGINEER), round 2 |
