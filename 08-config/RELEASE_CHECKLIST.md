# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)
### Deployment runbook URL
- Primary: `https://runbooks.taskq.internal/taskq-api/deploy-v4` (mirror: `sessi://taskq-final/runbooks/deploy-v4.md`).
- Step-by-step: tag → build → Alembic upgrade → systemd reload → smoke test (`curl /healthz`) → Gate 4 re-run.

### Rollback owner + on-call
- Primary rollback owner: release engineer on rotation; current rotation in PagerDuty schedule `taskq-release-oncall`.
- Secondary: Platform / SRE lead.
- Escalation: `#taskq-sre` Slack channel → on-call phone (in PagerDuty) → VP Engineering.
- See `08-config/CONFIG_RECORDS.md §7 Rollback SOP` for the exact `git checkout` + `alembic downgrade` + `systemctl reload` sequence.

### Post-release monitoring dashboard
- Grafana: `https://grafana.taskq.internal/d/taskq-api-overview` (panels: p99 latency, error rate, queue depth, worker saturation).
- Logs: structured JSON via Loki, label `service=taskq-api env=production`.
- Alerts: PagerDuty service `taskq-api-prod` (rules: p99 > 500ms for 5m, error rate > 1% for 5m, queue depth > 1000 for 10m).
- Synthetic check: `curl -fsS https://api.taskq.internal/healthz` every 60s from 3 regions.

### Customer comms template
Subject: `[taskq-api] Scheduled release v{{VERSION}} — {{DATE}}`

Body:
> Hi customers,
>
> We will deploy taskq-api **v{{VERSION}}** to production on **{{DATE}} at {{TIME}} UTC**. Expected downtime: none (rolling reload). New in this release:
>
> - {{BULLET_1}}
> - {{BULLET_2}}
> - {{BULLET_3}}
>
> Action required: none. If you observe any issue, contact support@taskq.internal or reply to this email. Status page: https://status.taskq.internal.
>
> — taskq-api release team
