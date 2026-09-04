# CONFIG_RECORDS.md - taskq-final

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260904-score98-33-g46b4841
- Git Commit: 46b4841
- Release Date: 2026-09-03

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | `uvicorn taskq_api.main:app --host 127.0.0.1 --port 8000 --reload`, SQLite at `./taskq.db`, `TASKQ_LOG_LEVEL=DEBUG`, `TASKQ_CORS_ORIGINS=*` (loopback only). |
| Production | `uvicorn taskq_api.main:app --host 0.0.0.0 --port 8000 --workers 4`, PostgreSQL via `TASKQ_DB_URL`, `TASKQ_LOG_LEVEL=INFO`, `TASKQ_LOG_FORMAT=json`, `TASKQ_CORS_ORIGINS=` (deny-all default per NFR-02). |

## 3. Dependency List
```
uvicorn==0.30.0          (BSD-3-Clause, direct)
fastapi==0.111.0         (MIT, direct)
pydantic==2.7.0          (MIT, direct)
pydantic-settings==2.2.1 (MIT, direct)
sqlalchemy==2.0.30       (MIT, direct)
alembic==1.13.0          (MIT, direct)
httpx==0.27.0            (BSD-3-Clause, direct)
import-linter==2.0       (MIT, direct)
mutmut==2.4.0            (MIT, direct)
pytest-benchmark==4.0.0  (BSD-3-Clause, direct)
pip-licenses==4.5.0      (MIT, direct)
anyio==4.3.0             (MIT, transitive)
click==8.1.7             (BSD-3-Clause, transitive)
h11==0.14.0              (MIT, transitive)
sniffio==1.3.1           (MIT, transitive)
starlette==0.37.2        (BSD-3-Clause, transitive)
typing-extensions==4.11.0 (PSF, transitive)
```
(Full lock at `requirements.lock`; mirror in `08-config/SBOM.json`.)

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| TASKQ_DB_URL | secret | SQLAlchemy DSN; password MUST NOT appear in logs (NFR-04). Dev default `sqlite:///./taskq.db`; prod uses PostgreSQL. |
| TASKQ_DB_POOL_SIZE | int | Connection pool size for the engine (default 5). |
| TASKQ_TASK_TIMEOUT | float | Single-task subprocess timeout in seconds (FR-08, default 10.0). |
| TASKQ_MAX_CONCURRENT | int | Background worker concurrency cap (default 8). |
| TASKQ_DRAIN_TIMEOUT | float | Graceful drain budget on shutdown in seconds (default 30.0). |
| TASKQ_RATE_BURST | int | Per-token rate-limit bucket capacity (FR-05, default 20). |
| TASKQ_RATE_PER_SEC | float | Per-token refill rate in tokens/second (FR-05, default 5.0). |
| TASKQ_HOST | string | Bind host; defaults to `127.0.0.1` (NFR-02 refuses external exposure by default). |
| TASKQ_PORT | int | Bind port (default 8000). |
| TASKQ_CORS_ORIGINS | string | Comma-separated allowed origins; empty = deny-all (NFR-02). |
| TASKQ_LOG_LEVEL | string | Log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`; default `INFO`). |
| TASKQ_LOG_FORMAT | string | `json` for structured logs (prod) or `console` for dev. |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-09-03 | harness-v4-20260904-score98-33-g46b4841 | `git tag` + signed tarball release, Alembic upgrade head, restart `uvicorn` workers via `systemctl reload taskq-api` | Release engineer on rotation (per P8 Human Context `## Rollback Owner`) |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | Initialized CONFIG_RECORDS baseline + appended Human Context (ownership / secret rotation / audit log). | Satisfies NFR-07 (license compliance evidence), NFR-04 (secrets policy), and Gate 1 traceability for config items. |

## 7. Rollback SOP
**Trigger Condition**: (a) Gate 4 fails post-deploy, (b) error rate exceeds 5% over 5 minutes, (c) health check `/healthz` returns non-200 for three consecutive 30s windows, OR (d) a TASKQ_* env var change causes startup failure.
**Commands**:
```bash
# 1. Pin to previous git tag
git checkout vharness-v4-20260904-score98-31

# 2. Roll back DB schema (if migration already applied)
alembic downgrade -1

# 3. Restore previous env file from secret store
./.venv/bin/python harness_cli.py secrets restore --env production --tag vharness-v4-20260904-score98-31

# 4. Reload service
systemctl reload taskq-api

# 5. Verify
curl -fsS http://127.0.0.1:8000/healthz && \
  ./.venv/bin/python harness_cli.py gate-check --gate 4
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)
### Ownership per config item
| Item | Owner | Backup | Contact channel |
|------|-------|--------|-----------------|
| `TASKQ_DB_URL` / `TASKQ_DB_POOL_SIZE` | Platform / SRE | Database admin | `#taskq-sre` Slack, PagerDuty `taskq-db` |
| `TASKQ_TASK_TIMEOUT` / `TASKQ_MAX_CONCURRENT` / `TASKQ_DRAIN_TIMEOUT` | Task runner team (FR-08 owner) | Service lead | `#taskq-runner` Slack |
| `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` | Auth/rate-limit team (FR-05 owner) | API lead | `#taskq-auth` Slack |
| `TASKQ_HOST` / `TASKQ_PORT` / `TASKQ_CORS_ORIGINS` | Security (NFR-02 owner) | Platform / SRE | `#sec-taskq` Slack |
| `TASKQ_LOG_LEVEL` / `TASKQ_LOG_FORMAT` | Observability team | Platform / SRE | `#taskq-obs` Slack |
| Source code (`taskq_api/`, `migrations/`) | Feature owners per FR | Release manager | GitHub `@taskq-final/maintainers` |

### Secret rotation cadence
- `TASKQ_DB_URL` (prod): rotate every 90 days; on-call triggers rotation within 24h of any suspected exposure.
- `TASKQ_CORS_ORIGINS` (not a secret but governed as one): reviewed every 30 days; changes require Security approval.
- API tokens used by CI to deploy (`TASKQ_DEPLOY_TOKEN`): rotate every 60 days via the secrets manager; rotation recorded in `.methodology/secret_rotation.log`.
- TLS certs: managed by infra (Let's Encrypt + 1Password vault); auto-renewed at 30d remaining.
- Rotation procedure: `.venv/bin/python harness_cli.py secrets rotate --env <env> --var <NAME>` (writes audit entry).

### Access audit log reference
- All secret reads / writes / rotations are appended to `sessi://taskq-final/secrets/audit.log` (also mirrored to `.methodology/access_audit.jsonl` in this repo).
- Quarterly access review: owner list above must re-confirm in `#taskq-sre` Slack; stale entries purged.
- Production env file is sealed via `git-crypt` and only readable by members of the GitHub team `taskq-final/prod-deployers`.
- Per-request audit trail for any config read in prod is emitted as structured JSON log line `event=secret.read var=... actor=... trace_id=...`.
