# Risk Mitigation Plans — taskq-final

> **Phase**: 7 — Risk Management | **Date**: 2026-09-04
> **Scope**: every risk with **L × I ≥ 9** (HIGH band) per `RISK_REGISTER.md`.
> **Format**: per-risk formal plan with owner, deadline, mitigation steps, exit criterion, residual statement.

---

## 1. Mitigation index

| Risk | Score | Plan below | Owner role | Target date |
|------|------:|------------|------------|-------------|
| R1  | 12 | §3.1 | Repository Lead | 2026-09-19 |
| R3  | 12 | §3.2 | Security Lead | 2026-09-12 |
| R4  | 9  | §3.3 | Security Lead | 2026-09-12 |
| R5  | 16 | §3.4 | Performance Lead | 2026-09-26 |
| R6  | 12 | §3.5 | Errors Lead | 2026-09-19 |
| R7  | 9  | §3.6 | Async Lead | 2026-09-19 |
| R8  | 9  | §3.7 | Async Lead | 2026-09-19 |
| R9  | 12 | §3.8 | Ops Lead | 2026-09-26 |
| R10 | 9  | §3.9 | Ops Lead | 2026-09-26 |
| R11 | 9  | §3.10 | Compliance Lead | 2026-09-26 |
| R14 | 9  | §3.11 | QA Lead | 2026-09-19 |
| R15 | 9  | §3.12 | Architecture Lead | 2026-10-03 |

---

## 2. Plan template (applied to each row below)

- **Risk** — ID + name + score
- **Owner** — accountable person / role (final assignee confirmed in `RISK_STATUS_REPORT.md`)
- **Deadline** — ISO date; later = lower-frequency review
- **Mitigation steps** — concrete, non-prescriptive-but-actionable tasks
- **Exit criterion** — verifiable condition (test pass / score ≥ threshold / etc.)
- **Monitoring** — how the residual stays visible
- **Residual acceptance** — explicit sign-off statement

---

## 3. Per-risk plans

### 3.1 R1 — v3 migration data loss (score 12)

- **Owner**: Repository Lead
- **Deadline**: 2026-09-19
- **Mitigation steps**:
  1. Maintain real-SQLite round-trip test that compares every row × column before upgrade vs after downgrade (FR-07).
  2. Lift line coverage on `migrations/versions/v3_split_results.py` from 66% (hot-spot 154–197, 214–262) to ≥ 90%; add named parametrized cases for malformed `finished_at` (now handled per T-15 fix) and edge rows that previously held `NULL`.
  3. Add a canary "downgrade→upgrade→downgrade" cycle as a separate pytest marker exercised in nightly CI, not every PR.
- **Exit criterion**:
  - `pytest 03-development/tests/integration/test_migration_v3_roundtrip.py -q` exits 0
  - `coverage report --include=03-development/src/migrations/versions/v3_split_results.py` ≥ 90%
- **Monitoring**: nightly migration cycle + Gate 4 dimension linting re-run before each release.
- **Residual acceptance**: rows persist exactly across one upgrade/downgrade cycle; loss-of-timestamp is acknowledged as a permanent loss only when `finished_at` was never recorded.

### 3.2 R3 — API key leakage (score 12)

- **Owner**: Security Lead
- **Deadline**: 2026-09-12
- **Mitigation steps**:
  1. Hash at rest (already implemented); constant-time compare at `service/auth.py`; plaintext emitted exactly once at provisioning.
  2. Authorize via chokepoint `require_scope` (FR-04); `service/auth.py:35` mutation survivor (R14) targeted in §3.11.
  3. Audit log line includes `principal.key_id` (T-13 resolved).
  4. No log line, response body, or traceback may embed a bearer token (already enforced by `redact_secrets` at `errors.py:22`); add a CI grep that fails on the literal string `sk_live` / test-provisioning plaintext prefix.
- **Exit criterion**:
  - `bandit -r 03-development/src/taskq_api/service/auth.py -ll` exits 0
  - reproduction test `tests/test_bug_hunt_t13_audit_log_principal.py` passes
- **Monitoring**: rotate test keys at each release; lint-imports forbids `print` outside `__main__`.
- **Residual acceptance**: plaintext visible only at provisioning, hash stored, scope-aware 403 / 401 issued, audit trail tied to principal.

### 3.3 R4 — 403 leaks resource existence (score 9)

- **Owner**: Security Lead
- **Deadline**: 2026-09-12
- **Mitigation steps**:
  1. Authorize-before-fetch invariant encoded in `api/deps.py:77` (`require_scope` resolves scope before repository lookup).
  2. Parametric test asserts identical 403 body for "task exists with wrong scope" vs "task absent".
  3. Audit log must not log differing trace depending on the existence path.
- **Exit criterion**:
  - `pytest tests/test_fr04.py -q` exits 0, including negative + positive parametric cases
  - `bandit -r 03-development/src/taskq_api -ll` exits 0
- **Monitoring**: regression on unauthorized leaks is enforced in nightly Fuzz-of-decoy-ID suite.
- **Residual acceptance**: 403 body, log line, and timing parity across both paths.

### 3.4 R5 — N+1 query on large tables (score 16)

- **Owner**: Performance Lead
- **Deadline**: 2026-09-26
- **Mitigation steps**:
  1. Every list endpoint explicitly uses `selectinload` / `joinedload` for related entities (FR-06 / FR-08).
  2. SQLAlchemy event listener asserts statement count is constant w.r.t. row count (NFR-01 / SPEC §8 #14).
  3. Benchmark suite `tests/bench/test_bench_task_repo.py` at 10 000-row fixture.
  4. Threshold: `GET /v1/tasks/{id}` p95 < 30ms; `GET /v1/tasks?limit=50` p95 < 80ms.
- **Exit criterion**:
  - `pytest tests/bench/ -q` exits 0 with the two p95 thresholds met.
  - Gate 3 score override `performance` ≥ 75.0.
  - `pytest -k stmt_count_invariant` exits 0 on 10 000-row seed.
- **Monitoring**: benchmark regressions vs the previous release are reported in `RELEASE_NOTES.md`.
- **Residual acceptance**: list-endpoint p95 stays inside SPEC §11 thresholds for the 10k-row seed; statement count stays constant.

### 3.5 R6 — error body leaks internal structure (score 12)

- **Owner**: Errors Lead
- **Deadline**: 2026-09-19
- **Mitigation steps**:
  1. RFC 7807 `application/problem+json` fixed fields + `detail` whitelist (FR-10).
  2. Disable Pydantic `include_input` in `ValidationError.errors(include_input=False)` (T-10 fix).
  3. `redact_secrets` runs on every problem body before serialisation.
  4. Regression test asserts no keys other than `type / title / status / detail / instance` appear; no traceback fragments appear.
- **Exit criterion**:
  - `pytest tests/test_fr10.py tests/test_bug_hunt_t10_pydantic_input_echo.py -q` exits 0
  - `bandit -r 03-development/src/taskq_api/api -ll` exits 0
- **Monitoring**: nightly fuzzing of validation paths against the schema.
- **Residual acceptance**: only RFC 7807 fields leave the boundary; opaque caller-supplied values never echo.

### 3.6 R7 — `CancelledError` swallowed (score 9)

- **Owner**: Async Lead
- **Deadline**: 2026-09-19
- **Mitigation steps**:
  1. Plain-text prohibition of swallowing `BaseException` / `CancelledError` (NFR-03).
  2. `_communicate_with_timeout` now kills the subprocess on `CancelledError` (T-07 fix landed).
  3. Drain path `app.py:140` and `runner.py:412` narrow `except Exception` is reviewed manually each release (R16 plan §3.16 below lists the manual review).
  4. Reproduction: `tests/test_bug_hunt_t07_subprocess_cancel.py`.
- **Exit criterion**:
  - reproduction test passes
  - `ast-error-handling` score does not regress below 80 across releases
  - `lint_imports` clean
- **Monitoring**: gate `error_handling` dimension regression watcher.
- **Residual acceptance**: Python 3.8+ `CancelledError` is `BaseException`-derived; any new swallow must be reviewed against the SAB exemption list.

### 3.7 R8 — orphan subprocess on timeout (score 9)

- **Owner**: Async Lead
- **Deadline**: 2026-09-19
- **Mitigation steps**:
  1. `proc.kill()` + `await proc.wait()` after timeout (FR-08 / SPEC §8 #25).
  2. Cover `runner.py:395–405` teardown paths with deterministic tests (currently untested, see `VERIFICATION_REPORT.md §5`).
  3. Integration test asserts no orphan subprocess holds a file descriptor after timeout.
  4. Track process tree via `/proc`-equivalent (psutil) in dev CI.
- **Exit criterion**:
  - line coverage on `runner.py:395–405` ≥ 100% (split out into named tests)
  - `pytest tests/test_fr08.py -q` exits 0
- **Monitoring**: nightly teardown test + smoke `pidof` parse in CI.
- **Residual acceptance**: every subprocess produced by `runner.py` is reaped (via `kill + wait`) before the request returns or `lifespan` exits.

### 3.8 R9 — forget migration post-deploy (score 12)

- **Owner**: Ops Lead
- **Deadline**: 2026-09-26
- **Mitigation steps**:
  1. `/readyz` fails closed when DB schema is below the expected Alembic head (FR-09 / SPEC §8 #11).
  2. Deployment runbook pre-flight runs `alembic current` vs `alembic heads`.
  3. The read-only repo-side smoke test (`make verify-system`) verifies migration head matches before serving traffic.
- **Exit criterion**:
  - reproduction test (downgrade a freshly-upgraded DB and probe `/readyz`) passes
  - `pytest tests/test_fr09.py -q` exits 0
- **Monitoring**: synthetic transaction probe in production `/readyz`.
- **Residual acceptance**: deploys without a migration step cannot reach the ready state.

### 3.9 R10 — connection pool exhaustion (score 9)

- **Owner**: Ops Lead
- **Deadline**: 2026-09-26
- **Mitigation steps**:
  1. `pool_pre_ping=True` on every checked-out connection (FR-06).
  2. `TASKQ_DB_POOL_SIZE=5` cap + concurrent execution cap `TASKQ_MAX_CONCURRENT=8` (FR-08).
  3. Stress test at 8× concurrent tasks asserts no "queue full" / timeout from the SQLAlchemy engine.
- **Exit criterion**:
  - stress test exits 0 with all 8 concurrent tasks completing inside the 10s timeout window
  - `pytest tests/test_fr06.py tests/test_fr08.py -q` exits 0
- **Monitoring**: in-memory `Engine.pool` stats surfaced via `/v1/metrics`.
- **Residual acceptance**: pool never hits zero-size under maximum load.

### 3.10 R11 — transitive license incompatibility (score 9)

- **Owner**: Compliance Lead
- **Deadline**: 2026-09-26
- **Mitigation steps**:
  1. Pinned `requirements.lock` + tree-wide `pip-licenses` scan in CI (NFR-07).
  2. Allowlist of accepted licences (MIT / BSD / Apache-2.0 / ISC / PSF / MPL-2.0).
  3. New dependency PRs require a `pip-licenses --format=markdown` snapshot attached.
- **Exit criterion**:
  - `pip-licenses --format=csv` lists zero non-allowlist licenses (transitive included)
  - License Compliance dimension 100/100 (already at Gate 4)
- **Monitoring**: every dependency bump triggers a fresh scan.
- **Residual acceptance**: only allowlist licenses remain in the dependency tree.

### 3.11 R14 — mutation survivor at `auth.py:35` (score 9)

- **Owner**: QA Lead
- **Deadline**: 2026-09-19
- **Mitigation steps**:
  1. Identify the equivalence class the un-killed mutant targets (likely an `and` / `or` branch or a constant swap inside the constant-time compare).
  2. Add a named test that kills the mutant; assert path via direct invocation, not through the framework route, so the equivalence class becomes observable.
  3. Re-run `mutmut` on the service layer, expect `survivor_count=0`.
- **Exit criterion**:
  - `mutmut` survivor count = 0 on `taskq_api/service/auth.py`
  - mutation score ≥ 97 (already at 96.3, this should push it past 97)
- **Monitoring**: per-release `mutmut` trend in `RELEASE_NOTES.md`.
- **Residual acceptance**: every mutant of the auth path is killed by a named test.

### 3.12 R15 — crg_cohesion_healthy calibration drift (score 9)

- **Owner**: Architecture Lead
- **Deadline**: 2026-10-03
- **Mitigation steps**:
  1. Document the calibration `0.2` in `.methodology/harness_config.json` with an in-line rationale and a reference to the Gate 4 challenger response.
  2. Capture `crg_baseline_p6.json` (already exists); re-baseline against HEAD as modules land.
  3. Add a sanity watchdog that fails CI if a future architecture commit drops cohesion below 0.2 *without* touching the calibration — that would be the calibration being used to hide drift.
  4. Periodic reset to framework default 0.3 in a separate dry-run job (does not gate).
- **Exit criterion**:
  - `crg_baseline_p6.json` committed and used as the strict-compare baseline
  - dry-run at default 0.3 publishes a delta report into `06-quality/`
- **Monitoring**: per-PR architecture diff vs baseline; per-release dry-run delta.
- **Residual acceptance**: calibration remains a deliberate, documented choice; no waiver hides drift.

---

## 4. Cross-reference

- `RISK_REGISTER.md` — full register + scoring rationale
- `RISK_STATUS_REPORT.md` — current status of every plan in this document
- `SPEC.md §9` — originating risk matrix
- `SPEC.md §11` — quantitative thresholds backing the exit criteria above

---

*End of mitigation plans. Each entry above must be reviewed at every Gate.*
