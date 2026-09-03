# Release Notes — taskq-final 1.0.0

> **Version**: 1.0.0
> **Release Date**: 2026-09-04
> **Phase**: 6 — Release
> **Gate 4 Status**: PASS
> **Gate 4 Composite Score**: **97.61** (`.methodology/quality_manifest.json` → `gate_results.gate4.overall_score`)

This is the first production release of `taskq-final`. The codebase has completed the full Phase 1–6 harness-methodology pipeline (P1 plan-all → P2 SAD/ADR/TEST_SPEC/SAB → P3 per-FR TDD → P4 Gate 3 testing → P5 per-FR delta cycle → P6 final quality gate) with Gate 4 PASS.

## Quality Snapshot

| Metric | Value | Source |
|--------|-------|--------|
| Gate 4 composite score | **97.61** | `.methodology/quality_manifest.json` → `gate_results.gate4.overall_score` |
| Gate 4 score (16-dim) | **97.6144** | `06-quality/QUALITY_REPORT.md` |
| Gate 3 composite score | 95.31 | `.methodology/quality_manifest.json` → `gate_results.gate3.overall_score` |
| Gate 2 composite score | 93.15 | `.methodology/quality_manifest.json` → `gate_results.gate2.overall_score` |
| Test coverage (`03-development/src`) | 97% (1198 stmts / 30 miss) | `05-verification/BASELINE.md` §3 |
| Mutation score (Gate 1) | 70.0 (override ≥ 70 PASS) | `.methodology/quality_manifest.json` → `gate_score_overrides.mutation_testing` |
| Mutation Testing (Gate 4 dim) | 96.3 | `06-quality/QUALITY_REPORT.md` → Mutation Testing |
| Unit + integration tests | 227 passed (unit) / 236 passed (integration) | `05-verification/VERIFICATION_REPORT.md` §1 |
| Bandit | 0 HIGH / 0 MEDIUM / 3 LOW | `05-verification/VERIFICATION_REPORT.md` §1 |
| Gitleaks | 0 real findings (2 documented FPs) | `05-verification/VERIFICATION_REPORT.md` §1 |
| `open_critical` / `open_high` | 0 / 0 | `06-quality/QUALITY_REPORT.md` |
| Architecture | 100.0 — 34 communities, 0 warnings | `06-quality/QUALITY_REPORT.md` (CRG) |

## Functional Requirements (all 10 PASS)

| FR ID | Title | Gate 1 Score | Status |
|-------|-------|--------------|--------|
| FR-01 | 任務資源 CRUD API | 100.0 | PASS |
| FR-02 | 任務執行端點 | 92.25 | PASS |
| FR-03 | API Key 認證 | 100.0 | PASS |
| FR-04 | Scope 授權 | 92.86 | PASS |
| FR-05 | 流量控制 | 100.0 | PASS |
| FR-06 | 持久化層與交易邊界 | 100.0 | PASS |
| FR-07 | Schema Migration (Alembic 三步演進) | 99.85 | PASS |
| FR-08 | 非同步執行器 | 100.0 | PASS |
| FR-09 | 健康檢查與可觀測性 | 94.5 | PASS |
| FR-10 | 錯誤契約 (RFC 7807) | 97.47 | PASS |

Per-FR scores are sourced from `.methodology/quality_manifest.json` → `gate_results.gate1.FR-XX.score`. Mean Gate 1 logic score = 97.69.

## Architecture Constraints

- `no_circular_dependencies` — enforced (`06-quality/QUALITY_REPORT.md` Architecture 100.0).
- Layering: `api > service > repository > models` (sqlalchemy imported only by repository). See NFR-06 in `.methodology/quality_manifest.json`.
- 34 CRG-detected code communities with no warning flags (`06-quality/QUALITY_REPORT.md` Architecture section).

## Non-Functional Requirements (NFR-01..NFR-12)

All 12 NFR dimensions are mapped to evaluation axes and recorded in `.methodology/quality_manifest.json` → `nfr_dimension_mapping` / `nfr_traceability`. Highlights:

- **NFR-01 (performance)**: `TaskRepository.get(task_id)` p95 < 30 ms and `TaskRepository.list(limit=50)` p95 < 80 ms against 10,000-row seed; list endpoint SQL statement count constant (no N+1 via `selectinload` / `joinedload`). Score: 75.0 (override) / 100.0 (Gate 4 dim).
- **NFR-04 (security, secret redaction)**: stdout_tail/stderr_tail/log/error-body secret regex → `[REDACTED]`; 0 DB connection-string leaks.
- **NFR-06 (architecture_constraints)**: 100.0 — no circular imports.
- **NFR-07 (license_compliance)**: 100.0 — all direct/transitive deps MIT/BSD/Apache-2.0/PSF; pinned with `==`.
- **NFR-08 (mutation_testing)**: 70.0 (Gate 1 override, ≥ 70 PASS) / 96.3 (Gate 4 dimension).
- **NFR-10 (integration_coverage)**: 80.0 (override) — driven only via `httpx.AsyncClient(ASGITransport)`.
- **NFR-12 (execute_verification_target)**: 100.0 — `make verify-system` exits 0.

## Changes Since Prior Phase Baseline

The previous persistent baseline (`.methodology/state.json` / `05-verification/BASELINE.md` at Phase 5 closure) recorded version 0.6.0 with Gate 1 per-FR PASS and Gates 2 & 3 closed. Subsequent Phase 6 work landed Gate 4 PASS at composite **97.61** and the trace-attestation refresh. Material changes since the 0.6.0 baseline:

| Date | Change | Commit |
|------|--------|--------|
| 2026-09-04 | chore: refresh trace attestation (Phase 6 preflight) | `5d23825` |
| 2026-09-04 | chore(gate1): record gate 1 evidence artifacts | `8e0f6a2` |
| 2026-09-04 | release(P6): commit FINAL_SIGN_OFF + RELEASE_NOTES for phase 6 milestone | `bb3f7d4` |
| 2026-09-04 | release(P6): Gate4 PASS score=97.6 — pipeline complete | `ea57ecc` |

> Commit subjects verified against `git log --format="%H %h %s"`. Gate 4 commit `ea57ecc` subject: "release(P6): Gate4 PASS score=97.6 — pipeline complete". Gate 4 score in the manifest is **97.61** (not 97.6 exactly — the commit message truncates to one decimal).

## Known Limitations

- **`migrations/versions/v3_split_results.py` 66% covered**: data-copy path lines 154-197 and 214-262 are exercised in the FR-07 integration suite (round-trip tests) but not in the unit suite; tracked, not gating (Gate 3 still closes at 95.31).
- **`taskq_api/service/runner.py` 95% covered**: lines 395-405 are subprocess teardown error paths; tracked, not gating.
- **3 LOW-severity bandit findings**: informational only (e.g. `subprocess` import in `service/runner.py`); 0 HIGH/MEDIUM.
- **2 gitleaks false positives**: `test_invalid_api_key_returns_401` / `test_missing_api_key_returns_401` strings in `.methodology/gate1_result.json` matched by `generic-api-key` entropy rule; methodology artefact, not source secret.
- **CRG dead-code advisory** (`06-quality/QUALITY_REPORT.md` §Dead Code Candidates): 23 advisory symbols flagged — these are framework callbacks / entry points (e.g. `healthz`, `readyz`, `dispatch`, `_handle_problem`, conftest fixtures) and are false positives; do not remove without verifying call graph.

## References

- Quality source-of-truth: `.methodology/quality_manifest.json`
- Gate 4 16-dimension breakdown: `06-quality/QUALITY_REPORT.md` (auto-generated by G4c)
- Verification provenance: `05-verification/VERIFICATION_REPORT.md`
- System baseline: `05-verification/BASELINE.md`