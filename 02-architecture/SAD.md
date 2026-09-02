# Software Architecture Document (SAD) — taskq-api

> Canonical source: `SPEC.md` (v1.0.0, 2026-07-30) — 10 FR / 12 NFR / 12 env.
> Requirements transcription: `01-requirements/SRS.md`.
> This SAD is the Phase 2 design baseline for the code delivered in `03-development/`.

---

## 1. Architecture Overview

`taskq-api` is an ASGI HTTP service (FastAPI, Python 3.11) that turns the task queue into a
REST resource: tasks are submitted, listed, executed and audited over HTTP; state is
persisted in a relational database (SQLite dev/test, PostgreSQL prod, one ORM model set);
the schema evolves through three real Alembic revisions; access is gated by hashed API keys,
per-token scopes and a DB-backed token bucket.

Four strict layers plus two independence modules:

```
        ┌──────────────────────────────────────────────┐
        │  api/      L4  FastAPI routers + deps        │  ← only layer that touches HTTP
        ├──────────────────────────────────────────────┤
        │  service/  L3  business logic, async runner  │  ← no Session, no sqlalchemy
        ├──────────────────────────────────────────────┤
        │  repository/ L2  Session + transactions      │  ← ONLY layer importing sqlalchemy
        ├──────────────────────────────────────────────┤
        │  models/   L1  ORM tables + pydantic schemas │
        └──────────────────────────────────────────────┘
   independence: config.py (TASKQ_* env)   errors.py (RFC 7807 + redaction)
   composition roots (outside the layers contract): app.py, __main__.py
```

| Decision | Value | Source |
|---|---|---|
| Runtime form | `uvicorn taskq_api.app:app`; admin CLI `python -m taskq_api` | SPEC §1 |
| Layering contract | `api > service > repository > models`, `sqlalchemy` forbidden outside `repository` | NFR-06 |
| Error contract | RFC 7807 `application/problem+json` for every non-2xx | FR-10 |
| Async model | `async def` endpoints + `asyncio.TaskGroup` background executor | FR-08 |
| Migration model | Alembic v1 → v2 → v3, every step reversible | FR-07 |

### 1.1 System Verification Target

**Makefile target**: `verify-system`

Chained steps (NFR-12): `alembic upgrade head` → full test suite → start the real service and
smoke `/healthz` + `/readyz` → `alembic downgrade base` → `alembic upgrade head` (round trip).
Prints `verify-system: PASS` and exits 0 only if every step exits 0; no step is prefixed with
`-` or suffixed with `|| true`.

**Exercises**: the delivered entry points (`uvicorn taskq_api.app:app` and
`python -m taskq_api migrate|healthcheck`) against a real SQLite file — so the four high-risk
modules run for real: `taskq_api.service.runner` (subprocess + drain),
`taskq_api.service.auth` (key hash compare), `taskq_api.repository.session` (transaction
boundary, pool pre-ping) and `migrations.versions.v3_split_results` (data migration both ways).
No autouse stub replaces them in this target.

---

## 2. Module Design

### 2.1 Directory Structure Design Principles (CRG compliance)

CRG scores one community per directory: cohesion = internal / (internal + external) ≥ 0.3 and
≤ 50 nodes. Design rules applied here:

| Check | Value in this design |
|---|---|
| Source directories | 5 (`taskq_api/`, `models/`, `repository/`, `service/`, `api/`) + `migrations/versions/` |
| Files per directory | root 5, models 3, repository 5, service 6, api 4, migrations/versions 4 — all ≤ 15 (NFR-11) |
| Hub per directory | yes (table below), ≥ 2 hub functions where the directory has ≥ 4 siblings |
| Entry points inside a hub dir | yes — `app.py` / `__main__.py` sit beside `config.py` + `errors.py` |
| Hub called from every function body | yes — required, not module-level only |
| Community size | ≤ 50 nodes per directory (largest is `service/`, ≈ 34 functions) |

| Directory | Hub module | Hub functions called by every sibling function body |
|---|---|---|
| `taskq_api/` | `config.py`, `errors.py` | `get_settings()`; `problem_detail()`, `redact_secrets()` |
| `models/` | `orm.py` | `status_values()`, `TaskStatus` (schema validators call both) |
| `repository/` | `session.py` | `transaction()`, `fetch_all()` / `fetch_one()` |
| `service/` | `common.py` | `now()`, `sanitize_text()` |
| `api/` | `deps.py` | `require_scope()`, `correlation_context()` |
| `migrations/versions/` | `_shared.py` | `table_exists()`, `copy_rows()` |

**Two modules are added beyond the SPEC §6 tree, and only these two** (flagged explicitly
rather than silently): `service/common.py` and `migrations/versions/_shared.py`. Both exist
because CRG requires a per-directory hub and both carry real shared logic —
`common.now()` is the single injectable UTC clock used by task timestamps, key
`revoked_at` checks, bucket refill and run durations; `common.sanitize_text()` is the FR-01
field rule set (non-empty, ≤ 1000 chars, injection blacklist) plus NFR-04 redaction applied to
`stdout_tail` / `stderr_tail`; `_shared.copy_rows()` is the row-copy primitive the v3 data
migration needs in both directions. No other file deviates from SPEC §6.

### 2.2 Module tree (SPEC §6 conformant)

```
03-development/src/taskq_api/
├── __init__.py
├── __main__.py        admin entry: migrate / key create / seed / healthcheck (FR-03, FR-07, FR-09)
├── app.py             FastAPI assembly, CORS, exception handlers, lifespan drain (FR-08/10, NFR-02)
├── config.py          TASKQ_* settings (independence)                          §5.1
├── errors.py          problem+json builders, domain errors, redaction (independence) FR-10, NFR-04
├── models/            L1
│   ├── orm.py         tasks, api_keys, tags, task_tags, task_results, rate_buckets  §5.2
│   └── schemas.py     TaskCreate / TaskOut / TaskListPage / RunOut / ProblemOut     FR-01
├── repository/        L2  (only layer importing sqlalchemy)
│   ├── session.py     engine, pool, transaction() CM, fetch helpers, ping()     FR-06, FR-09
│   ├── task_repo.py   keyset pagination, eager loading, results, delete cascade  FR-01/02, NFR-01
│   ├── key_repo.py    key rows by hash, revocation                              FR-03
│   └── rate_repo.py   bucket read-modify-write under row lock                    FR-05
├── service/           L3  (no Session, no sqlalchemy)
│   ├── common.py      now(), sanitize_text() — hub                               FR-01, NFR-04
│   ├── tasks.py       create / get / list / delete use cases                     FR-01
│   ├── runner.py      subprocess execution, TaskGroup, timeout, drain            FR-02, FR-08
│   ├── auth.py        key hashing, constant-time compare, scope order            FR-03, FR-04
│   └── ratelimit.py   token-bucket arithmetic + Retry-After                       FR-05
└── api/               L4
    ├── deps.py        single auth+scope+rate dependency, correlation context      FR-03/04/05
    ├── tasks.py       /v1/tasks*, /v1/tasks/{id}/run, /v1/tasks/{id}/runs         FR-01, FR-02
    └── health.py      /healthz, /readyz, /v1/metrics                              FR-09

migrations/
├── env.py
└── versions/{v1_initial,v2_tags,v3_split_results,_shared}.py                      FR-07
```

### 2.3 `taskq_api.config` — settings (independence)

| Attribute | Value |
|---|---|
| Responsibility | Read and type-coerce the 12 `TASKQ_*` variables; expose an immutable `Settings` |
| External interface | `get_settings() -> Settings` (cached), `Settings.db_url_safe` (password stripped) |
| Dependencies | stdlib `os`, pydantic settings only — imports no project module |

#### Logical Constraints
- Never logs or returns `TASKQ_DB_URL` verbatim; `db_url_safe` is the only form allowed to leave (NFR-04).
- Must not import `errors` (independence contract) and must not import any layer.

### 2.4 `taskq_api.errors` — problem+json + redaction (independence)

| Attribute | Value |
|---|---|
| Responsibility | Domain error types, RFC 7807 body builder, correlation ids, secret redaction |
| External interface | `problem_detail(type_, title, status, detail, instance, correlation_id)`, `redact_secrets(text)`, `new_correlation_id()`, exception classes `ValidationProblem` / `UnauthenticatedProblem` / `ForbiddenProblem` / `NotFoundProblem` / `ConflictProblem` / `RateLimitedProblem` / `NotReadyProblem` / `InternalProblem` |
| Dependencies | stdlib `re`, `uuid` only |

#### Logical Constraints
- `detail` is drawn from a fixed catalogue of strings; never interpolates SQL text, exception
  reprs, file paths or schema names (FR-10, NFR-02).
- `redact_secrets()` replaces the **whole line** matching
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` with `[REDACTED]` (NFR-04).
- Must not import `config` (independence) — the caller passes what it needs.

### 2.5 `taskq_api.models` (L1)

| Attribute | Value |
|---|---|
| Responsibility | `orm.py`: declarative tables of §5.2 + `TaskStatus`; `schemas.py`: pydantic v2 request/response models |
| External interface | `orm.Task/ApiKey/Tag/TaskTag/TaskResult/RateBucket`, `orm.status_values()`; `schemas.TaskCreate/TaskOut/TaskListPage/RunOut/ProblemOut/MetricsOut` |
| Dependencies | `orm.py` → sqlalchemy (legal: models is below repository and declares tables); `schemas.py` → pydantic + `orm.status_values()` |

#### Logical Constraints
- `schemas.py` holds no business rule beyond shape/bounds — `limit ≤ 200`, `command`/`name`
  length bounds; the blacklist and uniqueness live in `service` (FR-01).
- No module here imports `repository`, `service` or `api`.

### 2.6 `taskq_api.repository` (L2 — sole `sqlalchemy` importer)

| Attribute | Value |
|---|---|
| Responsibility | Engine/pool ownership, explicit transaction boundary, all SQL |
| External interface | `session.transaction() -> ContextManager[Session]`, `session.fetch_one/fetch_all`, `session.ping()`, `session.alembic_revision()`; `task_repo.create/get/list_page/delete/add_result/list_results/count_by_status`; `key_repo.find_active_by_hash`, `key_repo.insert`; `rate_repo.consume(key_id, burst, per_sec, now)` |
| Dependencies | `models`, `errors`, `config` |

#### Logical Constraints
- `transaction()` commits on clean exit, rolls back on any exception, always closes; every
  repository call runs inside one (FR-06, NFR-03).
- Engine built with `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True` (FR-06).
- No SQL text is ever assembled with f-strings, `%` or `+`; only ORM constructs and bound
  parameters (NFR-02).
- Relationship access is always explicitly preloaded (`selectinload`) so statement count per
  request is constant (NFR-01).
- `sqlalchemy.exc.IntegrityError` is translated to `errors.ConflictProblem` here — ORM
  exception types never cross the boundary (NFR-06).
- Returns detached plain dataclass/dict projections, never live ORM instances bound to a
  closed Session, so `service` cannot lazily trigger SQL.

### 2.7 `taskq_api.service` (L3)

| Attribute | Value |
|---|---|
| Responsibility | `tasks.py` CRUD use cases; `runner.py` async execution + lifecycle; `auth.py` authn/authz decisions; `ratelimit.py` bucket arithmetic; `common.py` clock + field sanitation |
| External interface | `tasks.create/get/list_tasks/delete`; `runner.submit(task_id) -> run_id`, `runner.drain(timeout)`; `auth.hash_key/verify(scope_required, key_scope)/issue_key`; `ratelimit.check(key_id) -> Allowed \| RetryAfter` |
| Dependencies | `repository`, `models.schemas`, `errors`, `config`, `common` |

#### Logical Constraints
- Holds no `Session` and imports no `sqlalchemy` symbol — enforced by the forbidden contract (NFR-06).
- `runner.py`: `asyncio.create_subprocess_exec(*shlex.split(command))`, never `shell=True`;
  `asyncio.wait_for(..., TASKQ_TASK_TIMEOUT)`; on timeout `process.kill()` then
  `await process.wait()` (no orphans, FR-08); concurrency bounded by
  `Semaphore(TASKQ_MAX_CONCURRENT)` with queued submissions — never an unbounded coroutine fan-out.
- `except asyncio.CancelledError: raise` is mandatory in every `runner.py` try block;
  no bare `except:` and no `except Exception: pass` anywhere (NFR-03).
- `auth.py` compares with `hmac.compare_digest` over SHA-256 digests and treats any row with
  non-null `revoked_at` as invalid (FR-03).
- Scope order `read < write < admin` lives in `auth.py` only.
- All output text that can echo a subprocess passes `common.sanitize_text()` before it reaches
  the repository (NFR-04).

### 2.8 `taskq_api.api` (L4)

| Attribute | Value |
|---|---|
| Responsibility | Route declaration, dependency wiring, HTTP status/model mapping — no business logic |
| External interface | `deps.require_scope(scope)`, `deps.correlation_context()`; routers `tasks_router`, `health_router` |
| Dependencies | `service`, `models.schemas`, `errors`, `config` |

#### Logical Constraints
- **Single authorisation point**: every `/v1/*` route depends on `deps.require_scope(...)`;
  authentication → scope check → rate limit happens there, in that order, *before* any
  resource lookup, so 403 cannot reveal existence (FR-04, R4). Asserted by
  `test_all_v1_routes_use_single_dependency`.
- Each handler ≤ 40 lines; every handler body is a call into `service` plus a response model
  construction (NFR-11).
- `/healthz` and `/readyz` carry neither the auth nor the rate-limit dependency (FR-03, FR-05);
  `/v1/metrics` requires `admin`.
- Every route declares OpenAPI `summary` and `description` (NFR-05).

### 2.9 Composition roots

| Module | Responsibility | Constraints |
|---|---|---|
| `app.py` | Build `FastAPI`, mount routers, register the 8 problem+json exception handlers, CORS from `TASKQ_CORS_ORIGINS` (empty ⇒ deny all), lifespan startup/shutdown that calls `runner.drain(TASKQ_DRAIN_TIMEOUT)` | Contains no business rule; handlers only translate `errors.*Problem` → status + body (FR-10) |
| `__main__.py` | `migrate`, `key create --scope`, `seed`, `healthcheck` | `key create` prints the plaintext exactly once and persists only the SHA-256 digest (FR-03, NFR-04) |

### 2.10 FR → module mapping (every FR owns ≥ 1 module)

| FR | Owning modules |
|---|---|
| FR-01 CRUD API | `api.tasks`, `service.tasks`, `service.common`, `repository.task_repo`, `models.schemas`, `models.orm` |
| FR-02 run endpoint / run history | `api.tasks`, `service.runner`, `repository.task_repo` |
| FR-03 API-key authentication | `api.deps`, `service.auth`, `repository.key_repo`, `__main__` |
| FR-04 scope authorisation | `api.deps`, `service.auth` |
| FR-05 rate limiting | `api.deps`, `service.ratelimit`, `repository.rate_repo` |
| FR-06 persistence + transactions | `repository.session`, `repository.task_repo`, `repository.key_repo`, `repository.rate_repo` |
| FR-07 Alembic three-step migration | `migrations.env`, `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results`, `migrations.versions._shared` |
| FR-08 async executor / drain | `service.runner`, `app` |
| FR-09 health, readiness, metrics | `api.health`, `repository.session` |
| FR-10 RFC 7807 error contract | `errors`, `app` |

### 2.11 Dependency graph — acyclic by construction

```
__main__ ─┬─> repository ─> models ─> (sqlalchemy)
          └─> service ─> repository
app ─> api ─> service ─> repository ─> models
 all layers ─> errors      all layers ─> config
 errors ─X─> config        config ─X─> errors      (independence pair)
 migrations.versions.* ─> migrations.versions._shared ─> (alembic op)
```

Edges only ever point downward (L4→L3→L2→L1) or into an independence module, and no
independence module imports anything project-local. Therefore no cycle exists; the
constraint is machine-checked by `lint-imports` (NFR-06) and by CRG's cycle report.
`migrations/` imports `models.orm` metadata inside `env.py` only — never the reverse.

---

## 3. Interfaces & Data Flows

### 3.1 HTTP surface

| Method | Path | Scope | Success | Error codes |
|---|---|---|---|---|
| POST | `/v1/tasks` | `write` | 201 + `TaskOut` | 401, 403, 409, 422, 429 |
| GET | `/v1/tasks/{id}` | `read` | 200 + `TaskOut` | 401, 403, 404, 429 |
| GET | `/v1/tasks?status=&limit=&cursor=` | `read` | 200 + `TaskListPage` | 401, 403, 422, 429 |
| DELETE | `/v1/tasks/{id}` | `admin` | 204 | 401, 403, 404, 429 |
| POST | `/v1/tasks/{id}/run` | `write` | 202 + `{run_id}` | 401, 403, 404, 429 |
| GET | `/v1/tasks/{id}/runs` | `read` | 200 + `[RunOut]` (newest first) | 401, 403, 404, 429 |
| GET | `/v1/metrics` | `admin` | 200 + `MetricsOut` | 401, 403, 429 |
| GET | `/healthz` | — | 200 `{"status":"ok"}` | — |
| GET | `/readyz` | — | 200 | 503 |

`limit` defaults to 50, maximum 200 (>200 ⇒ 422). Pagination is keyset/cursor-based: the
cursor is an opaque base64 of `(created_at, id)` and the query is
`WHERE (created_at, id) < (:c_ts, :c_id) ORDER BY created_at DESC, id DESC LIMIT :n` — no
`OFFSET` anywhere (FR-01, NFR-01).

### 3.2 Request pipeline (every `/v1/*` request)

```
client
  │ X-API-Key, body
  ▼
app.py  ── correlation id assigned ──────────────────────────────┐
  ▼                                                             │
api.deps.require_scope(required)                                 │
  ├─1 authenticate: sha256(key) → key_repo.find_active_by_hash   │
  │      miss / revoked ─────────────────> 401 problem+json      │
  ├─2 authorise: auth.verify(required, row.scope)                │
  │      insufficient ──────────────────-> 403 (no existence hint)
  ├─3 rate limit: ratelimit.check → rate_repo.consume (row lock) │
  │      empty bucket ──────────────────-> 429 + Retry-After     │
  ▼                                                             │
api.tasks handler (≤40 lines)                                    │
  ▼                                                             │
service.tasks / service.runner   (validation, state machine)     │
  ▼                                                             │
repository.<repo> inside session.transaction()                   │
  ▼                                                             │
models.orm  ──> SQLite / PostgreSQL                              │
                                                                 │
any errors.*Problem ──> app exception handler ──> problem+json ───┘
                                        + X-Correlation-Id header
```

Step ordering is load-bearing: the scope decision precedes every resource read, which is what
makes "403 must not disclose existence" structurally true rather than incidental.

### 3.3 Task execution flow (FR-02 / FR-08)

```
POST /v1/tasks/{id}/run
  → service.runner.submit(): task_repo.set_status(running) [txn]
                             run_id = uuid4
                             TaskGroup.create_task(_execute(...))  (Semaphore-bounded)
  → 202 {run_id}                          (response does not wait)

_execute():
  proc = await create_subprocess_exec(*shlex.split(command))     # never shell=True
  try:    rc = await wait_for(proc.wait(), TASKQ_TASK_TIMEOUT)
  except TimeoutError:  proc.kill(); await proc.wait(); status = timeout
  except CancelledError: raise                                   # never swallowed
  finally: tails = common.sanitize_text(stdout/stderr tail)       # NFR-04 before persist
  task_repo.add_result(exit_code, stdout_tail, stderr_tail, duration_ms, finished_at) [txn]
  task_repo.set_status(done | failed | timeout) [same txn]

shutdown (lifespan): runner.drain(TASKQ_DRAIN_TIMEOUT)
  → awaits in-flight tasks; still running at deadline ⇒ status = interrupted, subprocess killed
```

State machine: `pending → running → done | failed | timeout | interrupted`. Only `running`
tasks may transition; a second `run` on a `running` task is rejected by the service, so the
transition set stays total and side-effect free.

> Design note: the canonical SRS AC-2.3 set is `pending → running → done | failed | timeout`.
> The terminal `interrupted` state is a design-introduced elaboration required by FR-08
> graceful-drain (a `running` task still active at `runner.drain(TASKQ_DRAIN_TIMEOUT)` deadline
> is killed and marked `interrupted`); it is therefore part of the SAD §3.3 contract but not
> part of the SRS AC-2.3 enumeration.

### 3.4 Persistence model (§5.2) and its revisions

```
api_keys ─1───n─> rate_buckets            (key_id FK, one bucket per key)
   │
tasks ─1───n─> task_results               (v3; exit_code, stdout_tail, stderr_tail,
   │                                        duration_ms, finished_at)
   └─n───n─> tags  via task_tags          (v2; composite PK task_id+tag_id)

v1: tasks(id uuid, command, name, status, created_at, result_json), api_keys, rate_buckets
v2: + tags, task_tags, UNIQUE INDEX tasks.name
v3: + task_results, MOVE tasks.result_json rows → task_results, DROP tasks.result_json
```

Reversibility (FR-07): `v3.downgrade` re-creates `result_json`, copies every
`task_results` row back through `_shared.copy_rows()` column-by-column, then drops
`task_results`; `v2.downgrade` drops only what v2 added; `v1.downgrade` drops the three v1
tables. No `op.execute("DROP TABLE ...")` shortcut substitutes for a real downgrade.

### 3.5 Readiness decision (FR-09)

`/readyz` = `session.ping()` **AND** `session.alembic_revision() == script head`. Either check
failing yields 503 with a `detail` naming which one failed (`database unavailable` /
`migration not at head`) — fail closed, so deploying new code without running the migration is
never reported ready.

---

## 4. NFR Handling

| NFR | Dimension | Design mechanism | Latency / security / cost impact |
|---|---|---|---|
| NFR-01 | `performance` | Keyset pagination; `selectinload` eager loads; indexes on `tasks.created_at,id`, `tasks.status`, `task_results.task_id`; statement counter via SQLAlchemy `before_cursor_execute` listener in tests | Latency: p95 < 30 ms single get, < 80 ms 50-row list at 10k rows. Cost: constant statement count per request (2 for list) — no per-row query |
| NFR-02 | `security` | Hashed keys + `hmac.compare_digest`; scope-before-lookup; ORM/bound params only; CORS deny-by-default; no `shell=True`/`eval(`/`exec(`; fixed `detail` catalogue | Security: authn/authz/injection/disclosure all structurally closed. Cost: `bandit` 0 HIGH/0 MEDIUM plus two grep gates in CI |
| NFR-03 | `error_handling` | `transaction()` commit/rollback CM; explicit `except asyncio.CancelledError: raise`; no bare `except`; timeout kill + `await wait()`; migration failure leaves the previous revision intact | Reliability: no partial writes, no orphan processes, no hung shutdown. Latency: bounded by `TASKQ_TASK_TIMEOUT` / `TASKQ_DRAIN_TIMEOUT` rather than unbounded retry |
| NFR-04 | `security` | `errors.redact_secrets()` on the whole matching line; `Settings.db_url_safe` the only exportable DSN form; key plaintext printed once, never persisted | Security: no secret reaches disk, logs, `/v1/metrics` or an error body. Cost: one regex pass per tail/log record |
| NFR-05 | `documentation` | Every public function/class docstring cites `[FR-XX]`/`[NFR-XX]`; every route declares `summary` + `description`, asserted against `/openapi.json` | 100 % docstring coverage; no runtime cost |
| NFR-06 | `architecture_constraints` | `.importlinter` at repo root: layers `api > service > repository > models`, `config`/`errors` independence, forbidden `sqlalchemy` import outside `repository`; §2.11 acyclic graph | `lint-imports` exit 0. No wildcard `ignore_imports`, no contract downgrade, file is never deleted |
| NFR-07 | `license_compliance` | `requirements.txt` `==`-pinned, `requirements.lock` locks transitives; allowlist MIT / BSD-2 / BSD-3 / Apache-2.0 / PSF; `08-config/SBOM.json` records name/version/license/direct-or-transitive | Cost: full-tree `pip-licenses --format=json --with-system` scan in CI |
| NFR-08 | `mutation_testing` | `mutmut` scoped to `service/` + `repository/` (the two layers holding decision logic), reason recorded in `.methodology/harness_config.json` with `features.mutation_testing: true` | Score ≥ 70. Cost: scope limit is a runtime-budget decision, documented rather than silent |
| NFR-09 | `test_assertion_quality` | No `skip`/`skipif`/`xfail`/assert-less stub for any FR/NFR; FR-07 tested against a real on-disk SQLite file, round trip compared column-by-column; no `--ignore`/`-k`/`--deselect`/`collect_ignore` exclusion | skipped == 0, zero-assert == 0. `VERIFIED` in the traceability matrix only after a real pass |
| NFR-10 | `integration_coverage` | `httpx.AsyncClient(transport=ASGITransport(app))` only — handlers are never called directly; scenarios: full CRUD, one case each of 401/403/404/409/422/429/503, migration round trip, rate-limit trip and recovery, graceful drain | ≥ 80 % line coverage from `03-development/tests/integration/` |
| NFR-11 | `readability` | Handlers ≤ 40 lines (logic pushed into `service`), files ≤ 400 lines, ≤ 15 files per directory, per-function CC ≤ 10 | Project MI ≥ 80. Drives the §2.2 split — no god-module |
| NFR-12 | `execute_verification_target` | `make verify-system` chains migration → tests → live `/healthz` + `/readyz` smoke → `downgrade base` → `upgrade head` | exit 0 and `verify-system: PASS`; each step can fail the target |

Cross-cutting budgets: connection pool `TASKQ_DB_POOL_SIZE` (default 5) bounds DB cost;
`TASKQ_MAX_CONCURRENT` (default 8) bounds process/CPU cost; `TASKQ_RATE_BURST` /
`TASKQ_RATE_PER_SEC` bound per-caller cost; bind address defaults to `127.0.0.1` so nothing is
exposed without an explicit decision.

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key and `phase` as int must match
> `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste the canonical template and substitute real values.
> Validate: `python3 scripts/generate_sab.py --validate --project .`

**Status: AUTHORITATIVE.** The block below was emitted in the SAB Generation phase; its module
inventory is exactly §2.2 / §2.10 and its dependency edges exactly §2.11, so this SAD and the
generated `.methodology/SAB.json` cannot drift.

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-09-02"
  phase: 2
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - "taskq_api.api.deps"
        - "taskq_api.api.tasks"
        - "taskq_api.api.health"
      allowed_dependencies: ["service", "support"]
    - name: service
      modules:
        - "taskq_api.service.common"
        - "taskq_api.service.tasks"
        - "taskq_api.service.runner"
        - "taskq_api.service.auth"
        - "taskq_api.service.ratelimit"
      allowed_dependencies: ["repository", "support"]
    - name: repository
      modules:
        - "taskq_api.repository.session"
        - "taskq_api.repository.task_repo"
        - "taskq_api.repository.key_repo"
        - "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "support"]
    - name: models
      modules:
        - "taskq_api.models.orm"
        - "taskq_api.models.schemas"
      allowed_dependencies: ["support"]
    - name: support
      modules:
        - "taskq_api.app"
        - "taskq_api.__main__"
        - "taskq_api.config"
        - "taskq_api.errors"
      allowed_dependencies: ["api", "service", "repository"]
    - name: migrations
      modules:
        - "migrations.env"
        - "migrations.versions.v1_initial"
        - "migrations.versions.v2_tags"
        - "migrations.versions.v3_split_results"
        - "migrations.versions._shared"
      allowed_dependencies: []

  allowed_dependencies:
    - from: support
      to: api
    - from: support
      to: service
    - from: support
      to: repository
    - from: api
      to: service
    - from: api
      to: support
    - from: service
      to: repository
    - from: service
      to: support
    - from: repository
      to: models
    - from: repository
      to: support
    - from: models
      to: support

  quality_targets:
    max_complexity: 10
    min_coverage: 80
    max_coupling: 0.3

  nfr_dimension_mapping: {}

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "GET /v1/tasks/{id} p95 < 30ms and GET /v1/tasks?limit=50 p95 < 80ms at 10,000 rows; list endpoint SQL statement count constant (no N+1)"
      module: taskq_api.repository.task_repo
    NFR-02:
      type: security
      dimension: security
      target: "bandit -r 03-development/src/: 0 HIGH and 0 MEDIUM; grep for shell=True / eval( / exec( / string-concatenated SQL: 0 hits"
      module: taskq_api.api.deps
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "0 bare except / except Exception: pass; CancelledError always re-raised; every request transaction commits or rolls back via context manager"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security
      target: "100% of stdout_tail/stderr_tail/log/error-body lines matching the secret regex replaced with [REDACTED]; 0 occurrences of DB connection strings in logs, errors or /v1/metrics"
      module: taskq_api.errors
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% of public functions/classes have a docstring citing [FR-XX] or [NFR-XX]; every API endpoint has summary and description in /openapi.json"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0 with layers api > service > repository > models; sqlalchemy imported only by repository"
      module: taskq_api.repository.session
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "100% of direct and transitive dependencies pinned with == and licensed MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; 08-config/SBOM.json lists name/version/license/direct|transitive for each"
      module: taskq_api.config
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: ">=70 mutation score"
      module: taskq_api.service.tasks
      scope_layers: ["service", "repository"]
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "pytest skipped count == 0, xfail == 0, zero_assert == 0; FR-07 migrations tested against a real file-backed SQLite database"
      module: taskq_api.app
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: ">=80 line coverage from 03-development/tests/integration driven only via httpx.AsyncClient(ASGITransport)"
      module: taskq_api.app
    NFR-11:
      type: maintainability
      dimension: readability
      target: ">=80 maintainability index (LLOC-weighted); function CC <= 10; file <= 400 lines; directory <= 15 files; API handler <= 40 lines"
      module: taskq_api.api.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exits 0 and prints 'verify-system: PASS' after migration upgrade, full tests, health/ready smoke, and downgrade base + upgrade head round trip"
      module: taskq_api.app

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01: ["taskq_api.api.tasks", "taskq_api.service.tasks", "taskq_api.service.common", "taskq_api.repository.task_repo", "taskq_api.models.schemas", "taskq_api.models.orm"]
    FR-02: ["taskq_api.api.tasks", "taskq_api.service.runner", "taskq_api.repository.task_repo"]
    FR-03: ["taskq_api.api.deps", "taskq_api.service.auth", "taskq_api.repository.key_repo", "taskq_api.__main__"]
    FR-04: ["taskq_api.api.deps", "taskq_api.service.auth"]
    FR-05: ["taskq_api.api.deps", "taskq_api.service.ratelimit", "taskq_api.repository.rate_repo"]
    FR-06: ["taskq_api.repository.session", "taskq_api.repository.task_repo", "taskq_api.repository.key_repo", "taskq_api.repository.rate_repo"]
    FR-07: ["migrations.env", "migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results", "migrations.versions._shared"]
    FR-08: ["taskq_api.service.runner", "taskq_api.app"]
    FR-09: ["taskq_api.api.health", "taskq_api.repository.session"]
    FR-10: ["taskq_api.errors", "taskq_api.app"]

  architecture_constraints:
    - "no_circular_dependencies"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"

  required_artifacts:
    - ".importlinter"
    - ".env.example"
    - "requirements.txt"
    - "requirements.lock"
    - "alembic.ini"
    - "Makefile"
    - "08-config/SBOM.json"
```
<!-- SAB:END -->

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: parsed by `core/quality_gate/security_design.py:extract_security_block()`.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "unauthenticated HTTP input"
      description: "requests crossing from unauthenticated internet clients into the api layer, including body, query and CORS preflight"
    - id: TB-02
      name: "authenticated caller to privileged operation"
      description: "a holder of a read or write key reaching for an operation reserved to a higher scope"
    - id: TB-03
      name: "service to operating-system subprocess"
      description: "task command strings crossing from stored data into process execution"
    - id: TB-04
      name: "application to database"
      description: "statements and credentials crossing between the repository layer and SQLite/PostgreSQL"
    - id: TB-05
      name: "application to responses and logs"
      description: "text leaving the process towards HTTP clients, stdout logs and the metrics endpoint"
    - id: TB-06
      name: "deployment and schema state"
      description: "code and Alembic revision state crossing from a deploy pipeline into the running service"
  threats:
    - id: T-01
      boundary: TB-01
      category: spoofing
      description: "caller reaches /v1/* with no or a forged X-API-Key and acts as a legitimate token holder"
      mitigation: "single deps.require_scope dependency looks the SHA-256 digest up in api_keys and rejects miss, revoked or absent header with 401 problem+json"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_missing_api_key_returns_401"
    - id: T-02
      boundary: TB-01
      category: tampering
      description: "malformed or oversized payload mutates task state or smuggles injection characters into a stored command"
      mitigation: "pydantic TaskCreate bounds plus service.common.sanitize_text non-empty, <=1000 char and injection-blacklist rules, rejecting with 422"
      owner_module: "taskq_api.models.schemas"
      nfr: NFR-02
      verified_by: "test_task_crud_returns_201_422_404"
    - id: T-03
      boundary: TB-01
      category: denial_of_service
      description: "unthrottled request flood from one token exhausts connection pool and subprocess capacity"
      mitigation: "per-token DB-backed token bucket with TASKQ_RATE_BURST capacity returning 429 plus Retry-After, and TASKQ_MAX_CONCURRENT cap on execution"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-02
      verified_by: "test_rate_limit_burst_returns_429_with_retry_after"
    - id: T-04
      boundary: TB-01
      category: information_disclosure
      description: "hostile origin reads authenticated responses from a victim browser via permissive CORS"
      mitigation: "CORS denies every origin unless explicitly listed in TASKQ_CORS_ORIGINS, empty default means deny all"
      owner_module: "taskq_api.app"
      nfr: NFR-02
      verified_by: "test_cors_default_deny"
    - id: T-05
      boundary: TB-02
      category: elevation_of_privilege
      description: "write-scope key invokes the admin-only DELETE or metrics endpoint, or probes id existence through the rejection"
      mitigation: "scope hierarchy read<write<admin evaluated in the single dependency before any resource lookup, 403 body identical whether or not the id exists"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_write_key_admin_endpoint_returns_403_no_disclosure"
    - id: T-06
      boundary: TB-03
      category: elevation_of_privilege
      description: "command field carrying shell metacharacters executes attacker-chosen programs on the host"
      mitigation: "asyncio.create_subprocess_exec with shlex.split argv only, shell=True eval and exec forbidden repo-wide and grep-gated"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_subprocess_no_shell_true"
    - id: T-07
      boundary: TB-03
      category: denial_of_service
      description: "runaway task ignores its deadline and leaves orphan processes that consume the host after the request ends"
      mitigation: "asyncio.wait_for on TASKQ_TASK_TIMEOUT then process.kill followed by await process.wait, with graceful drain marking leftovers interrupted"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_task_timeout_kills_orphan_subprocess"
    - id: T-08
      boundary: TB-04
      category: tampering
      description: "attacker-controlled status, cursor or name value alters query semantics through string-built SQL"
      mitigation: "ORM constructs and bound parameters only, no f-string percent or plus SQL assembly, enforced by a grep gate over the source tree"
      owner_module: "taskq_api.repository.task_repo"
      nfr: NFR-02
      verified_by: "test_no_string_sql_concat"
    - id: T-09
      boundary: TB-04
      category: information_disclosure
      description: "database read or backup exposes reusable API key material"
      mitigation: "api_keys stores only the 64-hex SHA-256 digest, comparison uses hmac.compare_digest, plaintext is printed once at key create and never persisted"
      owner_module: "taskq_api.repository.key_repo"
      nfr: NFR-02
      verified_by: "test_api_keys_table_has_no_plaintext"
    - id: T-10
      boundary: TB-05
      category: information_disclosure
      description: "unexpected exception returns a stack trace, SQL statement, schema name or filesystem path to the caller"
      mitigation: "RFC 7807 bodies built from a fixed detail catalogue with no exception repr interpolation, 500 handler emits the generic internal problem"
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_500_detail_has_no_stack_trace"
    - id: T-11
      boundary: TB-05
      category: information_disclosure
      description: "database connection string including its password appears in logs, error bodies or the metrics response"
      mitigation: "config exposes only db_url_safe with credentials stripped and redact_secrets rewrites any line matching the postgres URL or token patterns to [REDACTED]"
      owner_module: "taskq_api.config"
      nfr: NFR-04
      verified_by: "test_db_url_not_in_logs"
    - id: T-12
      boundary: TB-05
      category: information_disclosure
      description: "subprocess stdout or stderr echoes a bearer token or secret key that is then persisted in task_results"
      mitigation: "service.common.sanitize_text applies the NFR-04 redaction regex line-wise before any tail is handed to the repository or a log record"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-04
      verified_by: "test_secret_redaction_regex"
    - id: T-13
      boundary: TB-02
      category: repudiation
      description: "a privileged action cannot be tied to the request that caused it because response and log share no identifier"
      mitigation: "correlation id generated per request, returned in X-Correlation-Id and in the problem body, and written to the structured log record"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_correlation_id_in_header_and_log"
    - id: T-14
      boundary: TB-06
      category: tampering
      description: "new code is deployed against an un-migrated schema and serves traffic against columns that do not exist"
      mitigation: "/readyz fails closed with 503 unless the database answers and alembic current equals script head, naming which check failed"
      owner_module: "taskq_api.api.health"
      nfr: NFR-03
      verified_by: "test_readyz_returns_503_when_migration_not_at_head"
    - id: T-15
      boundary: TB-06
      category: tampering
      description: "the v3 result_json split loses or corrupts existing rows, and the downgrade cannot restore them"
      mitigation: "_shared.copy_rows moves data column-by-column in both directions inside the migration transaction, verified by a real on-disk SQLite round trip"
      owner_module: "migrations.versions.v3_split_results"
      nfr: NFR-09
      verified_by: "test_v3_data_migration_round_trip_preserves_columns"
```
<!-- SEC:END -->

Note: `owner_module` values are exactly the modules §2.2/§2.10 declare (and that the §5 SAB
block must register); `nfr` ids all exist in `01-requirements/SRS.md`; each `verified_by` names
a single test already enumerated in `TEST_INVENTORY.yaml`, so the Phase 5 existence check has a
concrete target.
