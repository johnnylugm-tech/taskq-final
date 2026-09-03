# Risk Status Report — taskq-final

> **Phase**: 7 — Risk Management | **Date**: 2026-09-04
> **Snapshot basis**: Gate 4 quality manifest (`06-quality/QUALITY_REPORT.md`, Gate 4 score 97.61), `05-verification/VERIFICATION_REPORT.md`, `.methodology/bug_hunt_report.json`, `.methodology/mutation_survivors.json`, `.methodology/gate_timestamps.jsonl`.
> **Status legend**:
> - **Resolved** — current evidence shows no residual exposure; mitigation retained as a guardrail.
> - **Active (mitigated)** — mitigation in place; residual tracked; reviewer watches for regression.
> - **Active (residual)** — known residual with quantified acceptance statement.
> - **Accepted** — explicit, documented residual decision (e.g. calibration).
> - **Monitoring** — depends on a dimension score; no specific defect; cross-phase review.

---

## 1. Executive snapshot

| Metric | Value |
|--------|-------|
| Total risks tracked | 17 |
| HIGH (score ≥ 9) | 12 |
| Mitigations defined (HIGH band) | 12 / 12 (100%) |
| Owner assigned (HIGH band) | 12 / 12 |
| Gate 4 score | 97.61 / 100 |
| FRs Gate 1 PASS | 10 / 10 |
| Open bug-hunt critical/high | 0 (T-07, T-10, T-13, T-15 all resolved per `gate3_result.json` evidence) |
| Mutation score | 96.3 / 100 (≥ 70 threshold) |
| Mutation survivors | 1 (`auth.py:35` — see R14) |

---

## 2. Status table (HIGH band, ordered by score)

| ID | Name | Score | Status | Owner | Target date | Plan ref |
|----|------|------:|--------|-------|-------------|----------|
| R5  | N+1 查詢在大表上崩潰 | 16 | Active (mitigated) | Performance Lead | 2026-09-26 | §3.4 |
| R1  | v3 資料搬遷遺失資料 | 12 | Active (mitigated) | Repository Lead | 2026-09-19 | §3.1 |
| R3  | API key 洩漏 | 12 | Active (mitigated) | Security Lead | 2026-09-12 | §3.2 |
| R6  | 錯誤 body 洩漏內部結構 | 12 | Active (mitigated) | Errors Lead | 2026-09-19 | §3.5 |
| R9  | 部署後忘記跑 migration | 12 | Resolved | Ops Lead | 2026-09-26 | §3.8 |
| R4  | 403 洩漏資源存在性 | 9 | Resolved | Security Lead | 2026-09-12 | §3.3 |
| R7  | `CancelledError` 被吞 → 關死 | 9 | Active (mitigated) | Async Lead | 2026-09-19 | §3.6 |
| R8  | 任務 timeout 留下孤兒進程 | 9 | Active (mitigated) | Async Lead | 2026-09-19 | §3.7 |
| R10 | 連線池耗盡 | 9 | Active (mitigated) | Ops Lead | 2026-09-26 | §3.9 |
| R11 | transitive 依賴引入不相容 license | 9 | Active (mitigated) | Compliance Lead | 2026-09-26 | §3.10 |
| R14 | mutation survivor (auth.py:35) | 9 | Active (residual) | QA Lead | 2026-09-19 | §3.11 |
| R15 | crg_cohesion_healthy calibration | 9 | Accepted | Architecture Lead | 2026-10-03 | §3.12 |

MEDIUM / LOW band rows are tabulated in §4.

> HIGH band total: 12 / 12 with owner + target date + plan ref.

---

## 3. Per-risk status narrative (HIGH band)

### R1 — v3 migration data loss (score 12)

- **Status**: Active (mitigated)
- **Owner**: Repository Lead
- **Target**: 2026-09-19
- **Current evidence**: T-15 fix landed (no synthetic `now()` substitution). Integration round-trip test passes. Migration file coverage 66% — short of 90% target; tracked.
- **Open action**: lift coverage on `migrations/versions/v3_split_results.py` lines 154–197, 214–262.
- **Watch**: any new migration file.

### R3 — API key leakage (score 12)

- **Status**: Active (mitigated)
- **Owner**: Security Lead
- **Target**: 2026-09-12
- **Current evidence**: hash at rest + constant-time compare + `redact_secrets` in errors + audit log principal (T-13 resolved).
- **Open action**: CI grep guard for `sk_live` / provisioning plaintext prefix (per plan §3.2).

### R4 — 403 leaks resource existence (score 9)

- **Status**: Resolved
- **Owner**: Security Lead
- **Target**: 2026-09-12 (close-out)
- **Current evidence**: authorize-before-fetch invariant at `api/deps.py:77`. Negative + positive parametric test pairs present in `tests/test_fr04.py`.

### R5 — N+1 query on large tables (score 16)

- **Status**: Active (mitigated)
- **Owner**: Performance Lead
- **Target**: 2026-09-26
- **Current evidence**: `selectinload` / `joinedload` enforced; SQLAlchemy event listener asserts constant statement count; benchmark p95 inside SPEC §11 thresholds; `performance=75.0` recorded in Gate 3.

### R6 — error body leaks internal structure (score 12)

- **Status**: Active (mitigated)
- **Owner**: Errors Lead
- **Target**: 2026-09-19
- **Current evidence**: T-10 (Pydantic `include_input` echo) resolved; RFC 7807 fixed fields; `redact_secrets` registered.
- **Watch**: any new `exc.errors()` path.

### R7 — `CancelledError` swallowed (score 9)

- **Status**: Active (mitigated)
- **Owner**: Async Lead
- **Target**: 2026-09-19
- **Current evidence**: T-07 fix landed; `runner.py:279` and `runner.py:412` are SAB-exempt drain paths but reviewed each release.

### R8 — orphan subprocess on timeout (score 9)

- **Status**: Active (mitigated)
- **Owner**: Async Lead
- **Target**: 2026-09-19
- **Current evidence**: `proc.kill() + await proc.wait()`. Teardown lines `runner.py:395–405` not yet directly covered.
- **Open action**: deterministic tests on `runner.py:395–405`.

### R9 — forget migration post-deploy (score 12)

- **Status**: Resolved
- **Owner**: Ops Lead
- **Target**: 2026-09-26 (maintenance review)
- **Current evidence**: `/readyz` fail-closed at Alembic-head mismatch; FR-09 test suite green.

### R10 — connection pool exhaustion (score 9)

- **Status**: Active (mitigated)
- **Owner**: Ops Lead
- **Target**: 2026-09-26
- **Current evidence**: `pool_pre_ping=True`; `TASKQ_DB_POOL_SIZE=5`; `TASKQ_MAX_CONCURRENT=8`.

### R11 — transitive license incompatibility (score 9)

- **Status**: Active (mitigated)
- **Owner**: Compliance Lead
- **Target**: 2026-09-26
- **Current evidence**: License Compliance dimension 100/100 at Gate 4; `pip-licenses` allowlist wired in CI.

### R14 — mutation survivor (auth.py:35) (score 9)

- **Status**: Active (residual)
- **Owner**: QA Lead
- **Target**: 2026-09-19
- **Current evidence**: 1 survivor (`bad_survived`) per `.methodology/mutation_survivors.json`. Mutation score 96.3 still ≥ 70 threshold; the survivor is not blocking but is the seed of an explicit improvement.

### R15 — crg_cohesion_healthy calibration (score 9)

- **Status**: Accepted
- **Owner**: Architecture Lead
- **Target**: 2026-10-03
- **Current evidence**: calibration `0.2` documented and committed; `crg_baseline_p6.json` in place; framework default `0.3` flagged as a separate dry-run job.

---

## 4. Status table (MEDIUM / LOW band)

| ID | Name | Score | Status | Owner | Target | Notes |
|----|------|------:|--------|-------|--------|-------|
| R2  | SQL injection | 8 | Monitoring | n/a | n/a | T-08 refuted; ORM-only path; CI grep + bandit gate active. |
| R16 | broad_swallow antipatterns | 6 | Active (mitigated) | Async Lead | 2026-09-19 | Score 85 ≥ 80 threshold; reviewed alongside R7. |
| R17 | integration-suite collection failure | 4 | Resolved | QA Lead | closed | `setup.cfg norecursedirs` makes integration opt-in. |
| R12 | rate bucket 競態 | 3 | Resolved | n/a | n/a | Single transaction + row-level lock; FR-05 test green. |
| R13 | gitleaks FP class | 2 | Active (recommendation) | n/a | n/a | Recommended addition to `.gitleaksignore`; see `VERIFICATION_REPORT.md §5`. |

---

## 5. Trend / deltas since last gate close

| Indicator | Gate 3 | Gate 4 | Δ |
|-----------|-------:|-------:|--:|
| Quality score | 95.31 | 97.61 | +2.30 |
| Mutation score | 70.0 | 96.3 | +26.3 |
| Mutation survivors | n/a (per-FR-only baseline) | 1 | n/a |
| Bug-hunt high-severity open | 4 (T-07, T-10, T-13, T-15) | 0 | -4 |
| `error_handling` dimension | n/a (Gate 3 scope) | 85 | first measurement |
| `arch_check` warnings | n/a | 0 | clean |
| dead-code candidates (advisory) | n/a | 23 | first count |

The deltas above were taken from `.methodology/gate_timestamps.jsonl`, `gate3_result.json`, and `gate4_result.json`. They are reproducible from commit hashes in `state.json`.

---

## 6. Cross-reference

- `RISK_REGISTER.md` — authoritative register (R1–R17)
- `RISK_MITIGATION_PLANS.md` — formal owner + deadline + exit criterion (HIGH band)
- `06-quality/QUALITY_REPORT.md` — source of dimension scores feeding R14, R15, R16
- `05-verification/VERIFICATION_REPORT.md` — source of coverage hot-spots feeding R1, R8
- `.methodology/bug_hunt_report.json` — closed findings feeding R6, R7, R1, R3
- `.methodology/mutation_survivors.json` — feeds R14
- `SPEC.md §9` — originating risk matrix
- `FINAL_SIGN_OFF.md`, `RELEASE_NOTES.md` — release-time consumers of this status

---

## 7. Action checklist for next milestone

1. ☐ Repository Lead — raise migration line coverage to 90% (R1).
2. ☐ Security Lead — wire CI grep on `sk_live` plaintext (R3); confirm R4 close-out evidence.
3. ☐ Async Lead — add deterministic tests for `runner.py:395–405` (R8); review R7/R16 SAB exemption list at release.
4. ☐ Ops Lead — stress-test 8× concurrency (R10); document `/readyz` procedure (R9).
5. ☐ Compliance Lead — attach `pip-licenses` snapshot to next dep-change PR (R11).
6. ☐ QA Lead — kill `auth.py:35` mutant (R14).
7. ☐ Architecture Lead — publish dry-run cohesion delta at default 0.3 (R15).

---

*End of status report. Regenerate when a Gate closes or any risk transitions state.*
