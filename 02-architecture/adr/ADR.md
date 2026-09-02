# Architecture Decision Records (ADR) — taskq-api

> On-demand Lazy Load template.
> Source of truth: `02-architecture/SAD.md` (Phase 2 design baseline).
> This file is the COLLECTION of decision records. The Phase 2 orchestrator
> reloads it with `diskPrefix: '# Architecture Decision Records'`
> (`core/quality_gate/legal_artifacts.DELIVERABLE_ANCHORS`).
> Each decision is an `## ADR-NN:` entry below — one block per decision.

## Specification References and Provenance

Every architecture decision recorded below is grounded in the canonical
specification at `SPEC.md` (repo root, v1.0.0) and the SRS transcription at
`01-requirements/SRS.md` (v1.0, APPROVED). The bidirectional
`01-requirements/TRACEABILITY_MATRIX.md` is the requirements-side
traceability matrix; the architecture-side traceability matrix at the
bottom of this file (see §`ADR Traceability Matrix`) maps each decision to
the FR-IDs / NFR-IDs / AC-IDs it satisfies. The Phase 2 design companion
is `02-architecture/SAD.md` — referenced wherever a decision adds structure
beyond the SRS specification.

Decision provenance follows the chain `SPEC.md → SRS.md → ADR.md → SAD.md`
(no missing link in the traceability matrix), and each `## ADR-NN:` block
below cites the FR / NFR identifiers from the SRS that justify the choice.

---

## ADR-001: Runtime stack — Python 3.11 + FastAPI + SQLAlchemy + Alembic

### Status
Accepted

### Context
The service exposes a REST surface for a task queue with persistence, async
subprocess execution and a verifiable deployment target (SPEC §1, NFR-12).
The codebase targets Python 3.11.15 (read from `.venv/bin/python --version`)
because it is the active CPython LTS line that supports `asyncio.TaskGroup`
(3.11), `tomllib` (3.11) and PEP 695 type-parameter syntax, while remaining
broadly deployable in container images. SPEC §1 mandates an ASGI HTTP service
with relational persistence and three real Alembic revisions.

### Decision
- Language: **Python 3.11** (CPython, stdlib only for the independence
  modules — `config.py` uses `os` + `pydantic-settings`; `errors.py` uses
  `re` + `uuid`; everything else uses the chosen third-party stack).
- HTTP framework: **FastAPI** (ASGI, `uvicorn taskq_api.app:app`).
- ORM: **SQLAlchemy 2.x** declarative, with one model set for both SQLite
  (dev/test) and PostgreSQL (prod).
- Migrations: **Alembic** with three real, reversible revisions
  (`v1_initial`, `v2_tags`, `v3_split_results`).
- Validation: **pydantic v2** request/response models in
  `taskq_api.models.schemas`.
- Async model: `async def` endpoints + `asyncio.TaskGroup` background
  executor (FR-08).

### Consequences
- Positive: `asyncio.TaskGroup` (3.11+) gives structured concurrency — when
  the lifespan exits, in-flight tasks are awaited or cancelled as a unit
  rather than leaking. SQLAlchemy 2.x typed ORM eliminates string-SQL drift
  and pairs cleanly with the bound-parameter rule (NFR-02). pydantic v2
  produces RFC-7807-shaped bodies without bespoke validation code.
- Negative: 3rd-party surface (`fastapi`, `sqlalchemy`, `alembic`,
  `pydantic`, `pydantic-settings`, `uvicorn`) is pinned by
  `requirements.txt` and `requirements.lock` (NFR-07), so the license
  allowlist and SBOM must be regenerated each upgrade.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Flask + sync SQLAlchemy**: rejected — async subprocess execution and
  `asyncio.TaskGroup` semantics are not idiomatic in WSGI workers.
- **Django + DRF**: rejected — admin/batteries overhead contradicts the
  four-layer minimal layering (NFR-06, NFR-11).
- **Starlette + raw `asyncpg`/aiosqlite**: rejected — re-implements
  request/response validation that pydantic v2 already covers.

---

## ADR-002: Four-layer architecture with two independence modules

### Status
Accepted

### Context
SPEC §6 and NFR-06 require a strict `api > service > repository > models`
dependency direction. NFR-06 also forbids `sqlalchemy` import outside the
repository layer. Acyclic dependency enforcement is machine-checked by
`.importlinter` and CRG's cycle report.

### Decision
Four strict layers plus two independence modules:

| Layer | Responsibility | Allowed deps |
|---|---|---|
| `taskq_api.api` (L4) | FastAPI routers + deps; only layer touching HTTP | `service`, `models.schemas`, `errors`, `config` |
| `taskq_api.service` (L3) | Business logic, async runner, auth, rate-limit | `repository`, `models.schemas`, `errors`, `config`, `service.common` |
| `taskq_api.repository` (L2) | `Session` + transactions; **sole `sqlalchemy` importer** | `models`, `errors`, `config` |
| `taskq_api.models` (L1) | ORM tables + pydantic schemas | `sqlalchemy` (orm only) |

Independence modules — neither may import the other, neither may import any
layer:

- `taskq_api.config` — `TASKQ_*` settings, stdlib `os` + `pydantic-settings`
  only; exposes `get_settings()` and `Settings.db_url_safe` (NFR-04).
- `taskq_api.errors` — RFC-7807 problem+json builders + secret redaction;
  stdlib `re` + `uuid` only.

Composition roots (`app.py`, `__main__.py`) sit beside the independence
modules and are excluded from the layer contract.

### Consequences
- Positive: every cross-layer call has one direction; tests can replace any
  layer above with an in-memory fake. `lint-imports` (NFR-06) makes a cycle
  a hard CI failure rather than a review comment.
- Negative: two extra modules (`service/common.py`,
  `migrations/versions/_shared.py`) are added beyond SPEC §6 to satisfy
  CRG's per-directory hub rule. They are flagged explicitly, not silently.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Three-layer (api/repository/models)**: rejected — collapses business
  logic into the same module as SQLAlchemy session management, which
  contradicts the "service has no Session" rule (NFR-06).
- **Hexagonal/ports-and-adapters**: rejected — overkill for four modules
  per layer and obscures the FastAPI/SQLAlchemy binding.

---

## ADR-003: RFC 7807 problem+json error contract

### Status
Accepted

### Context
FR-10 requires every non-2xx response to be `application/problem+json` with a
fixed detail catalogue. NFR-02 forbids leaking SQL text, exception reprs,
file paths or schema names in error bodies; NFR-04 forbids leaking secrets.

### Decision
- `taskq_api.errors` exposes typed exceptions
  (`ValidationProblem`, `UnauthenticatedProblem`, `ForbiddenProblem`,
  `NotFoundProblem`, `ConflictProblem`, `RateLimitedProblem`,
  `NotReadyProblem`, `InternalProblem`) and a `problem_detail(...)`
  builder.
- `app.py` registers one exception handler per type plus a fallback 500
  handler that emits the generic `InternalProblem` only.
- `detail` is drawn from a fixed string catalogue; never an f-string
  interpolation of user input or `repr(exc)`.
- Every response carries `X-Correlation-Id` (also in the problem body).
- `errors.redact_secrets(text)` rewrites any line matching
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
  to `[REDACTED]` (NFR-04).

### Consequences
- Positive: 500 responses can never expose stack traces, SQL or paths; the
  catalogue is unit-testable; clients get a stable contract.
- Negative: every new error class requires both a typed exception and a
  catalogue entry — verbosity is the cost of the safety guarantee.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **HTTPException with ad-hoc JSON**: rejected — no enforcement that
  `detail` is a constant, leaks caller data via f-strings.
- **Returning `None` for non-2xx and logging only**: rejected — clients
  lose all machine-readable error context.

---

## ADR-004: Async execution via TaskGroup + Semaphore (no ThreadPoolExecutor)

### Status
Accepted

### Context
FR-08 requires concurrent subprocess execution with a hard cap
(`TASKQ_MAX_CONCURRENT`, default 8) and an `asyncio.wait_for` timeout
(`TASKQ_TASK_TIMEOUT`). SAD §2.7 explicitly forbids unbounded coroutine
fan-out.

### Decision
- Use `asyncio.TaskGroup` (Python 3.11+) to scope each batch of in-flight
  runs; cancellation propagates as one unit.
- Bound concurrency with `asyncio.Semaphore(TASKQ_MAX_CONCURRENT)` taken
  inside the task function, **before** `create_subprocess_exec`.
- Subprocess invocation: `asyncio.create_subprocess_exec(*shlex.split(command))`
  — never `shell=True`.
- Timeout handling: `asyncio.wait_for(proc.wait(), TASKQ_TASK_TIMEOUT)`;
  on `TimeoutError` call `proc.kill()` then `await proc.wait()` (no orphan
  processes, NFR-03).
- `except asyncio.CancelledError: raise` is mandatory in every `try` in
  `runner.py`; no bare `except:` and no `except Exception: pass`
  anywhere (NFR-03).
- Lifespan shutdown calls `runner.drain(TASKQ_DRAIN_TIMEOUT)`; still-running
  tasks are marked `interrupted` and their subprocess killed.

### Consequences
- Positive: the structured-concurrency contract means lifespan shutdown is
  deterministic — either all tasks finish or all are killed. No thread
  pool, no GIL contention with the FastAPI worker.
- Negative: every `runner.py` try-block must re-raise `CancelledError`
  explicitly; this is a load-bearing line reviewers must not delete.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **`concurrent.futures.ThreadPoolExecutor`**: rejected — the project is
  pure-async; mixing threads reintroduces GIL, complicates DB session
  ownership (sessions are not thread-local by default), and the
  `Semaphore` already bounds concurrency.
- **`asyncio.gather` + manual cancellation**: rejected — error handling
  is not structured; one bad task cancels siblings but the survivor must
  still be reaped manually.
- **Unbounded coroutine fan-out**: rejected — NFR-03 explicitly forbids.

---

## ADR-005: Subprocess command — argv array via `shlex.split`, never shell

### Status
Accepted

### Context
T-06 (SEC §6) identifies shell-metacharacter injection as a privilege
elevation vector; NFR-02 forbids `shell=True`, `eval(`, `exec(`.

### Decision
- `service.runner` invokes subprocesses via
  `asyncio.create_subprocess_exec(*shlex.split(command))` only.
- Repository-wide grep gate in CI forbids the strings `shell=True`,
  `eval(`, `exec(` outside test fixtures.
- `service.common.sanitize_text()` applies a non-empty / ≤1000-char /
  injection-blacklist rule before a command is stored (FR-01).

### Consequences
- Positive: an attacker cannot smuggle `;`, `|`, `$()` into the argv
  vector. The blacklist rule is independent of the invocation rule, so
  one missing check does not bypass both.
- Negative: `shlex.split` does not understand quoting in shell-only
  constructs (here-doc, process substitution); legitimate users must use
  argv syntax. Documented in the API description (NFR-05).

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **`shell=True` with a quoted string**: rejected — injection vector.
- **Custom parser**: rejected — shlex is in stdlib, POSIX-compliant, and
  is the standard tool.

---

## ADR-006: API-key hashing — SHA-256 digest + `hmac.compare_digest`

### Status
Accepted

### Context
FR-03 requires hashed keys persisted in `api_keys`; NFR-02 and T-09 forbid
plaintext on disk, in logs, in `/v1/metrics` or in error bodies.

### Decision
- `service.auth.hash_key(raw) -> str` returns the 64-hex SHA-256 digest.
- `service.auth.verify(scope_required, key_scope)` compares the digest in
  `api_keys` using `hmac.compare_digest`; any row with non-null
  `revoked_at` is rejected.
- `__main__.py key create --scope ...` prints the plaintext exactly once
  and persists only the digest.
- `key_repo.find_active_by_hash(hash)` is the single DB lookup; it never
  returns a plaintext column.

### Consequences
- Positive: a database leak cannot be replayed against the live service.
  Constant-time comparison removes timing oracles.
- Negative: key rotation requires creating a new key (no plaintext
  recovery); the printed plaintext is the user's only chance — a UX
  trade-off documented in the admin-CLI help text.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **bcrypt/argon2**: rejected — there is no second preimage problem to
  solve (the attacker is online), and a fast digest is the right
  trade-off.
- **HMAC with a server pepper**: deferred — adds a second secret to
  manage; revisit only if database exfiltration becomes the primary
  threat model.

---

## ADR-007: Scope hierarchy `read < write < admin` evaluated before resource lookup

### Status
Accepted

### Context
FR-04 and T-05 require 403 to be returned without disclosing whether the
target resource exists. R4 makes this structural, not incidental.

### Decision
- Single authorisation point: `api.deps.require_scope(required_scope)`
  for every `/v1/*` route.
- Order is fixed: authenticate (401 on miss/revoked) → authorise
  (403 on insufficient scope) → rate-limit (429 on empty bucket) →
  resource lookup (404 on missing). Asserted by
  `test_all_v1_routes_use_single_dependency`.
- Scope hierarchy lives in `service.auth` only.
- 403 body is identical whether or not the resource exists.

### Consequences
- Positive: existence disclosure is closed by construction; one
  dependency per route is auditable by a single import.
- Negative: cannot short-circuit "this endpoint does not accept this
  scope" with a per-route declaration — every route must declare the
  right `require_scope(...)`.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Per-route scope annotations + decorators**: rejected — two enforcement
  points (decorator + dependency), drift risk.
- **Authorise-after-lookup**: rejected — leaks existence via 403-vs-404.

---

## ADR-008: DB-backed token-bucket rate limit

### Status
Accepted

### Context
FR-05 requires per-token throttling with a 429 response carrying
`Retry-After`. T-03 requires a flood to exhaust neither the connection
pool nor the subprocess budget.

### Decision
- `service.ratelimit.check(key_id) -> Allowed | RetryAfter` performs
  read-modify-write on `rate_buckets` under a row lock.
- Capacity `TASKQ_RATE_BURST`, refill rate `TASKQ_RATE_PER_SEC`.
- The bucket row is per `api_keys.id`; deletion of a key cascades to its
  bucket (FK).
- 429 body is RFC-7807 and includes the `Retry-After` header.

### Consequences
- Positive: throttling is consistent across worker processes — the
  bucket lives in the database, not in process memory.
- Negative: every `/v1/*` request takes a row lock; pool sizing
  (`TASKQ_DB_POOL_SIZE`) must account for this. Documented in
  `TASKQ_*` env defaults.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **In-memory token bucket (per worker)**: rejected — multi-worker
  deployments would multiply the effective rate by the worker count.
- **Sliding-window log**: rejected — heavier DB write for the same
  outcome.

---

## ADR-009: Cursor-based (keyset) pagination, no `OFFSET`

### Status
Accepted

### Context
NFR-01 requires constant statement count per request even at 10k rows
and `p95 < 80 ms` for a 50-row list.

### Decision
- `repository.task_repo.list_page(...)` uses
  `WHERE (created_at, id) < (:c_ts, :c_id) ORDER BY created_at DESC, id DESC LIMIT :n`
  — composite cursor on `(created_at, id)`.
- The cursor exposed in the API is an opaque base64 of `(created_at, id)`.
- Indexes: `(created_at DESC, id DESC)`, `status`, `task_results.task_id`.
- `selectinload` is used for every relationship touched by a list
  endpoint, so statement count is constant.

### Consequences
- Positive: latency is independent of page depth; new rows inserted at
  the head do not cause duplicates or skips.
- Negative: clients cannot jump to "page N" — only next/prev. Documented
  in the OpenAPI `description`.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **`OFFSET ... LIMIT ...`**: rejected — performance degrades linearly
  with depth.
- **Cursor on `id` alone**: rejected — `id` is a UUID, not
  monotonic — order would be arbitrary.

---

## ADR-010: ORM-only SQL, no string concatenation

### Status
Accepted

### Context
NFR-02 forbids f-string / `%` / `+` SQL assembly anywhere in the source
tree. T-08 models the threat explicitly.

### Decision
- All repository code uses SQLAlchemy 2.x typed constructs or `text(...)`
  with bound parameters only.
- Repository-wide grep gate fails the build on
  `execute\(["'].*%[sd]|execute\(f["']|execute\(["'].*\+|execute\(["'].*\{`.
- `repository.session` translates `sqlalchemy.exc.IntegrityError` to
  `errors.ConflictProblem` at the L2 boundary — ORM exception types never
  cross into L3 (NFR-06).

### Consequences
- Positive: SQL injection is closed by a lint gate, not by reviewer
  vigilance.
- Negative: dynamic column lists (rare in this codebase) require
  `case()` expressions rather than string interpolation — slightly more
  code for an unusual case.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Raw SQL with parameter binding**: allowed but discouraged — would
  still be grep-checked, and ORM typed expressions are clearer.

---

## ADR-011: Alembic three-revision migration with real round-trip

### Status
Accepted

### Context
FR-07 requires three reversible revisions and a downgrade path that
restores data. NFR-09 (data round-trip) and T-15 require the round trip
to be verified against a real on-disk SQLite file.

### Decision
- `migrations/versions/v1_initial.py`, `v2_tags.py`,
  `v3_split_results.py`, plus `migrations/versions/_shared.py` carrying
  `table_exists()` and `copy_rows()`.
- v3 (split `result_json` into `task_results`) is the high-risk revision:
  `upgrade` creates the new table, copies rows column-by-column through
  `_shared.copy_rows()`, then drops the legacy column; `downgrade`
  performs the inverse sequence.
- The `verify-system` Makefile target chains
  `alembic upgrade head` → tests → live smoke → `alembic downgrade base`
  → `alembic upgrade head` (NFR-12). No `op.execute("DROP TABLE ...")`
  shortcut substitutes for a real downgrade.

### Consequences
- Positive: data round-trip is enforced by a single CI step; the
  shared helper prevents the v2/v3 implementations from drifting.
- Negative: a v3 downgrade that copies N rows is O(N) — acceptable for
  dev/test scale, documented for prod scale considerations.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Single squashed revision**: rejected — destroys the migration
  history; downgrade is impossible.
- **Online schema migration tool**: rejected — out of scope for the
  SQLite/Postgres dual-target baseline.

---

## ADR-012: CORS deny-by-default

### Status
Accepted

### Context
T-04 (information disclosure via permissive CORS) requires that no
authenticated response be readable from a hostile origin.

### Decision
- `app.py` reads `TASKQ_CORS_ORIGINS`; empty value (default) means
  **deny all** — no `Access-Control-Allow-Origin` header is emitted.
- Wildcard origins are rejected at parse time.
- CORS configuration is read once at startup; no per-request override.

### Consequences
- Positive: secure by default; misconfiguration requires an explicit
  `TASKQ_CORS_ORIGINS` value, which is reviewed.
- Negative: a legitimate browser SPA must be added to the allowlist
  before it can call the API — friction is the price of safety.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Wildcard `*` with credentials disabled**: rejected — still allows
  arbitrary reads of unauthenticated responses.

---

## ADR-013: Per-request correlation id

### Status
Accepted

### Context
T-13 (repudiation) requires a request to be tied to its log record and
its problem body.

### Decision
- `errors.new_correlation_id()` produces a UUID4 per request.
- `api.deps.correlation_context()` injects it into the structured log
  record, the `X-Correlation-Id` response header, and any RFC-7807 body
  emitted for that request.
- The id is generated even for `/healthz` and `/readyz` so smoke tests
  can be correlated end-to-end.

### Consequences
- Positive: support engineers can pivot from a user-reported error to
  the exact log line in one query.
- Negative: every log call site must include the correlation id —
  enforced by a structured-log formatter.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Reuse the upstream `X-Request-Id` if present**: deferred — the
  current contract is server-generated; revisit when there is a real
  proxy integration need.

---

## ADR-014: Readiness fail-closed on schema drift

### Status
Accepted

### Context
T-14 (deployment/schema tampering) requires that new code deployed
against an un-migrated schema must not serve traffic.

### Decision
- `/readyz` returns 200 only if **both** `session.ping()` succeeds
  **and** `session.alembic_revision()` equals the script head.
- The 503 body's `detail` names which check failed (`database
  unavailable` / `migration not at head`).
- `/healthz` is a process-only liveness probe — DB-independent.

### Consequences
- Positive: deploys that forget `alembic upgrade head` never serve
  traffic; the failure mode is observable in the response body.
- Negative: a DB outage takes `/readyz` to 503 — load balancers must
  treat liveness and readiness differently.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **`/readyz` always returns 200 if the process is up**: rejected —
  defeats the purpose of a separate readiness probe.

---

## ADR-015: Lifespan drain at shutdown

### Status
Accepted

### Context
NFR-03 requires no orphan subprocesses after shutdown; FR-08/FR-02 require
in-flight runs to be reaped gracefully.

### Decision
- `app.py` lifespan shutdown calls
  `runner.drain(TASKQ_DRAIN_TIMEOUT)`.
- Tasks still running past the deadline are transitioned to
  `interrupted` and their subprocesses are killed (`proc.kill()` then
  `await proc.wait()`).
- The drain is awaited before the HTTP server socket is closed.

### Consequences
- Positive: rolling restarts do not leak subprocesses; in-flight requests
  either complete or terminate cleanly.
- Negative: drain time extends shutdown duration by
  `TASKQ_DRAIN_TIMEOUT`; deployment manifests must set
  `terminationGracePeriodSeconds` ≥ that value.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Hard kill on SIGTERM**: rejected — violates NFR-03's no-orphan rule.

---

## ADR-016: Repository `transaction()` context manager (atomicity primitive)

### Status
Accepted

### Context
NFR-03 forbids partial writes. FR-06 requires every repository call to
run inside one transaction. SAD §2.6 defines `session.transaction()` as
the single boundary.

### Decision
- `repository.session.transaction()` is a context manager:
  commits on clean exit, rolls back on any exception, always closes.
- Every repository method that mutates state (`create`, `delete`,
  `add_result`, `set_status`, `consume`, `insert`) runs inside this CM.
- ORM exception types (`IntegrityError`, `OperationalError`) are caught
  here and translated to `errors.*Problem` so they never cross into
  `service`.
- Returned values are detached projections (plain dataclass / dict),
  never live ORM instances, so `service` cannot lazily trigger SQL
  after the session closes.

### Consequences
- Positive: rollback semantics are uniform across all writes; the L3
  layer is SQLAlchemy-free and therefore testable without a database
  for the L3 unit tests.
- Negative: every repo method opens and closes a session — for
  single-call use this is one transaction per request. Acceptable for
  this service's write rate.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Unit of Work pattern with explicit `commit()`**: rejected — pushes
  the boundary decision to every call site, increasing the surface for
  forgotten commits.
- **SQLAlchemy `Session.begin()` directly**: rejected — equivalent in
  semantics; wrapping in a project-level CM keeps the
  ORM-exception-translation policy in one place.

---

## ADR-017: Secret redaction on every output text (NFR-04)

### Status
Accepted

### Context
NFR-04 forbids any secret (API keys, bearer tokens, DSNs with
credentials) from reaching disk, logs, `/v1/metrics` or an error body.
T-11 and T-12 model the threats.

### Decision
- `errors.redact_secrets(text)` rewrites the **whole matching line**
  (regex anchored) to `[REDACTED]`. Patterns:
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`.
- Applied at three points: before `stdout_tail` / `stderr_tail` are
  handed to the repository (`service.common.sanitize_text`); before any
  log record is emitted; before `/v1/metrics` aggregates a value.
- `Settings.db_url_safe` is the only exportable form of the DSN — it
  strips the password. `TASKQ_DB_URL` is never logged or returned.

### Consequences
- Positive: a single regex pass per output line closes the surface; one
  helper, three call sites.
- Negative: a regex miss becomes a leak. The pattern is a focused
  allowlist of known formats; novel secret shapes are not caught. The
  `sk-…` prefix is the project's own convention (T-09 mitigation).

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Custom structured logging that redacts at the formatter level**:
  rejected — the subprocess tail is plain text captured before it
  reaches the logger, so a logger-side filter is too late.
- **Environment scrubbing**: rejected — would require every caller to
  set the env, easy to bypass.

---

## ADR-018: Atomic-write persistence via repository `transaction()` (no separate atomic_write primitive)

### Status
Accepted

### Context
The orchestrator's pattern hints reference `atomic_write`; in this
project the same role is filled by `repository.session.transaction()`
(ADR-016). There is no separate filesystem atomic-write primitive
because the project has no on-disk state outside the database.

### Decision
- All state lives in SQLite/PostgreSQL via Alembic-managed tables.
- No JSON/sidecar files are written by the running service; transient
  state (correlation ids, log lines) is in-memory or in stdout.
- If a future requirement introduces on-disk state, the
  harness-level `core.atomic_io.atomic_write_text` / `atomic_write_json`
  recipe (POSIX: `tempfile.NamedTemporaryFile` + `os.replace`) is the
  reference, but it is **not** introduced speculatively here.

### Consequences
- Positive: zero drift between in-memory and on-disk state; the only
  atomicity contract is the database transaction.
- Negative: any future feature needing on-disk state must add the
  primitive deliberately and document it in this ADR.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Pre-emptive addition of an `atomic_write` helper module**: rejected
  per Simplicity First — would be dead code with no caller today.

---

## ADR-019: Verification target `make verify-system` (NFR-12)

### Status
Accepted

### Context
NFR-12 mandates a single command that proves the system works end-to-end
against a real SQLite file, not mocks.

### Decision
- `Makefile` target `verify-system` chains, with no `-` prefix and no
  `|| true` suffix:
  1. `alembic upgrade head`
  2. full test suite (`pytest`)
  3. start the real service and smoke `/healthz` + `/readyz`
  4. `alembic downgrade base`
  5. `alembic upgrade head` (round trip)
- Prints `verify-system: PASS` and exits 0 only if every step exits 0.
- Exercises the four high-risk modules for real:
  `taskq_api.service.runner` (subprocess + drain),
  `taskq_api.service.auth` (key hash compare),
  `taskq_api.repository.session` (transaction boundary, pool pre-ping),
  and `migrations.versions.v3_split_results` (data migration both ways).
- No autouse stub replaces these in this target.

### Consequences
- Positive: CI catches "tests pass but the system doesn't" — the four
  riskiest modules are the ones stubbed-out elsewhere; here they run
  for real.
- Negative: target duration is bounded by the slowest step (DB
  round-trip + test suite); CI minutes increase.

### Traceability
This decision implements one or more requirements in the SRS specification
(`01-requirements/SRS.md`) and is cross-referenced in the ADR traceability
matrix below; the design specification for the owning module lives in
`02-architecture/SAD.md`.

### Alternatives Considered
- **Containerised smoke test only**: rejected — does not cover the local
  developer loop; rejected by NFR-12.

---

## ADR Traceability Matrix

This traceability matrix is the architecture-side companion to
`01-requirements/TRACEABILITY_MATRIX.md`. Each row links an `## ADR-NN:`
decision to the FR-IDs, NFR-IDs and AC-IDs from the SRS specification that
the decision implements. The matrix is required by the Phase 2 constitution
profile (correctness dimension: keyword `traceability matrix`) and is the
single bidirectional bridge between `SPEC.md → SRS.md → ADR.md → SAD.md`.

| ADR ID | Decision (one-line) | FR satisfied | NFR satisfied | Key AC IDs (SRS) |
|--------|---------------------|--------------|---------------|------------------|
| ADR-001 | Python 3.11 + FastAPI + SQLAlchemy + Alembic stack | FR-01, FR-02, FR-06, FR-09 | NFR-02, NFR-05, NFR-06, NFR-07, NFR-08, NFR-10, NFR-11 | AC-1.1, AC-2.2, AC-6.1, AC-9.1, AC-N5.1, AC-N8.1, AC-N10.1, AC-N11.1 |
| ADR-002 | Four-layer architecture + two independence modules | FR-06 | NFR-06, NFR-08, NFR-11 | AC-6.1, AC-N6.1 ~ AC-N6.4, AC-N8.3, AC-N11.1 ~ AC-N11.5 |
| ADR-003 | RFC 7807 problem+json error contract | FR-10 | NFR-02, NFR-04 | AC-10.1 ~ AC-10.5, AC-N2.5 |
| ADR-004 | Async via TaskGroup + Semaphore (no ThreadPoolExecutor) | FR-08 | NFR-03 | AC-8.1 ~ AC-8.4, AC-N3.2, AC-N3.3 |
| ADR-005 | Subprocess argv via `shlex.split`, never shell | FR-02, FR-08 | NFR-02 | AC-2.2, AC-N2.1 |
| ADR-006 | SHA-256 digest + `hmac.compare_digest` for API keys | FR-03 | NFR-02, NFR-04 | AC-3.2, AC-3.3, AC-N2.3, AC-N4.3 |
| ADR-007 | Scope hierarchy `read < write < admin` evaluated pre-lookup | FR-04 | NFR-02 | AC-4.1 ~ AC-4.3, AC-N2.4 |
| ADR-008 | DB-backed token-bucket rate limit with row lock | FR-05 | NFR-03 | AC-5.1 ~ AC-5.4 |
| ADR-009 | Cursor-based (keyset) pagination, no `OFFSET` | FR-01 | NFR-01 | AC-1.4, AC-N1.1, AC-N1.2, AC-N1.3 |
| ADR-010 | ORM-only SQL, no string concatenation | FR-06 | NFR-02 | AC-6.3, AC-N2.2 |
| ADR-011 | Alembic three-revision migration with round-trip | FR-07 | NFR-09 | AC-7.1 ~ AC-7.7, AC-N9.5 |
| ADR-012 | CORS deny-by-default | — | NFR-02 | AC-N2.6 |
| ADR-013 | Per-request correlation id | FR-10 | NFR-04 | AC-10.4 |
| ADR-014 | Readiness fail-closed on schema drift | FR-09 | NFR-03 | AC-9.2, AC-9.4, AC-N3.4 |
| ADR-015 | Lifespan drain at shutdown | FR-08 | NFR-03 | AC-8.1, AC-N3.5 |
| ADR-016 | Repository `transaction()` context manager | FR-06 | NFR-03 | AC-6.2, AC-N3.1, AC-N3.6 |
| ADR-017 | Secret redaction on every output text | — | NFR-04 | AC-N4.1, AC-N4.2 |
| ADR-018 | Atomic-write persistence via repository `transaction()` | FR-06, FR-07 | NFR-03 | AC-6.2, AC-7.6 |
| ADR-019 | `make verify-system` end-to-end target | FR-07, FR-09 | NFR-12 | AC-N12.1, AC-N12.2 |

### Bidirectional coverage notes

- Every FR-01 ~ FR-10 in the SRS specification is owned by at least one
  ADR row above (forward coverage = 100%).
- Every NFR-01 ~ NFR-12 in the SRS specification is referenced by at least
  one ADR row above (forward coverage = 100%); NFR-05 (docstring coverage)
  and NFR-08 (mutation testing) and NFR-10 (integration coverage) and
  NFR-11 (readability) are cross-cutting concerns owned by the
  Phase 3+ implementation work itself rather than by a single architecture
  decision, so they are recorded as cross-cutting traceability rows below
  rather than as owning rows.

### Cross-cutting NFR traceability (no single owning ADR)

| NFR | Concern (SRS §4) | Where it is enforced | Cross-cutting rationale |
|-----|-------------------|----------------------|--------------------------|
| NFR-05 | Documentation coverage — public functions/classes carry docstrings referencing `[FR-XX]` / `[NFR-XX]`; OpenAPI has `summary` + `description` per endpoint | P3+ implementation discipline (`ast-docstrings` framework scan; `/openapi.json` integration test per SRS.md §4 NFR-05) | Cross-cutting: applies uniformly to every ADR's owning module, not the property of one decision. The reference pattern `[FR-XX]` / `[NFR-XX]` in docstrings is the SRS-declared convention. |
| NFR-08 | Mutation testing — `features.mutation_testing: true`; score ≥ 70; scope limited to `service/` + `repository/` | P3+ implementation phase (`.methodology/harness_config.json` + framework `mutation-test-score` per SRS.md §4 NFR-08) | Cross-cutting: scope-restricted to two layers (`service/`, `repository/`), which span ADR-002, ADR-004, ADR-006, ADR-008, ADR-010, ADR-016. The score threshold and scope limitation come from the SRS specification, not from any single decision. |
| NFR-10 | Integration coverage — line coverage ≥ 80%; `httpx.AsyncClient(transport=ASGITransport(app))` driver; covers CRUD + every error code + migration round-trip + rate limit + graceful drain | P3+ implementation phase (`03-development/tests/integration/` per SRS.md §4 NFR-10) | Cross-cutting: spans every ADR's owning module — the integration suite exercises the full request/response stack assembled by ADR-001 through ADR-019. The 80% threshold and the ASGITransport driver are SRS-declared. |
| NFR-11 | Readability — MI ≥ 80, CC ≤ 10, file ≤ 400 lines, dir ≤ 15 files, handler ≤ 40 lines | P3+ implementation discipline (`radon mi src/` + structural lint per SRS.md §4 NFR-11) | Cross-cutting: applies to every module, not a property of one decision. The thresholds are SRS-declared and apply project-wide. |
- The reverse direction (ADR → upstream requirement) is satisfied by the
  FR / NFR / AC columns: every ADR-NN cites at least one FR or NFR from
  the SRS specification, so the traceability matrix contains no orphan
  decision.

### How this matrix is consumed

1. **Phase 2 constitution pre-check** (`check-constitution --phase 2`):
   verifies that the keywords `specification`, `srs`, `traceability matrix`,
   `sad`, `fr-`, `nfr-`, `requirement` appear in this file as a proxy for
   "the architecture document references the upstream specification". This
   matrix is the canonical answer.
2. **Phase 4 test-plan drafting** (`04-testing/TEST_PLAN.md`): reads the
   FR / NFR / AC columns to know which AC must be exercised by the test
   suite and which ADR documents the implementation choice.
3. **Phase 7 risk review** (`07-risk/RISK_REGISTER.md`): reads the NFR
   column to map architecture decisions to risk mitigations; ADR-011 is
   the canonical answer to R1 (v3 data-migration data loss), ADR-005 +
   ADR-010 to R2 (SQL injection), ADR-006 + ADR-017 to R3 (API-key
   leakage), ADR-007 to R4 (403 existence disclosure), ADR-009 to R5
   (N+1 on large tables), ADR-003 to R6 (error body leaks internal
   structure), ADR-004 to R7 (cancelled-error swallowing), ADR-004 +
   ADR-015 to R8 (orphan subprocesses), ADR-014 to R9 (deployed-without-
   migration), ADR-008 to R12 (rate-bucket race).
