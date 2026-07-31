# Monitoring a deployment

Per-instance health hooks the public repo ships (BOP-035 / deployment-model §4.4).
**Alerting hookup is deploy-side** — ntfy, Cloud Monitoring, or whatever WebDrvn
runs outside this repository. This page only names the signals.

## What to watch

| Signal | Where | Healthy when |
|--------|--------|--------------|
| Liveness / readiness | `GET /readyz` → `{"status":"ok"}` | HTTP 200. Unauthenticated; load balancers and the fleet-upgrade verify step use this. |
| Instance detail | `GET /statusz` | `status: "ok"`. Requires a **viewer+** bearer **or** `X-Status-Key: $STATUS_INTERNAL_KEY` (fail closed). |
| Cron freshness | `/statusz` → `last_cron` | `stale: false` — last milestone cron finished successfully within 24h. |
| Migration pin | `/statusz` → `migrations` | `matched: true` (image head == DB `alembic_version`). |
| Error rate | Cloud Logging | Filter on `severity>=ERROR` for `service="brokerops-api"`. |

### `/statusz` payload (compact)

```json
{
  "status": "ok",
  "version": "v0.2.0",
  "orchestrator": "langgraph",
  "selectors": {
    "crm": "followupboss",
    "email": "ses",
    "sms": "stub",
    "files": "google_drive",
    "extraction": "deterministic",
    "drafting": "deterministic"
  },
  "migrations": {
    "current": "0012",
    "head": "0012",
    "matched": true,
    "mode": "database"
  },
  "last_cron": {
    "job": "milestones",
    "outcome": "success",
    "finished_at": "2026-07-31T13:00:00+00:00",
    "checked": 12,
    "skipped_pending_escalation": 1,
    "email_tail_suppressed": 0,
    "error": null,
    "age_seconds": 3600,
    "stale": false
  },
  "uptime_seconds": 86400.5
}
```

- **Selectors are names only** — never API keys, DSNs, or base URLs.
- **`status: "degraded"`** when migrations don't match **or** the milestone cron is
  stale (never run, older than 24h, or last outcome was `failure`).
- Unauthenticated detail is **not** exposed: probe with an operator session token
  or the internal status key Terraform can inject as `STATUS_INTERNAL_KEY`.

`/healthz` remains container-internal only (Google reserves that path on
`*.run.app`); external checks always use `/readyz`.

## Structured logs

On Cloud Run (or whenever `LOG_FORMAT=json` / `K_SERVICE` is set) the API emits
**one JSON object per line** on stderr. Fields:

| Field | Meaning |
|-------|---------|
| `severity` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` (Cloud Logging) |
| `message` | Log text (uvicorn access lines include method/path/status) |
| `logger` | Python logger name (`uvicorn.access`, `brokerops_api.…`) |
| `time` | ISO-8601 UTC |
| `service` | Always `brokerops-api` — stable filter across clients |
| `version` | `IMAGE_VERSION` when Terraform pins the release (BOP-031) |

Local compose stays human-readable text unless you set `LOG_FORMAT=json`.

Example log-based alert filters (deploy-side):

- Error spike: `resource.type="cloud_run_revision" jsonPayload.service="brokerops-api" severity>=ERROR`
- Release pin: group by `jsonPayload.version`

## Cron success signal

`POST /internal/cron/milestones` (Cloud Scheduler) records its outcome into the
`cron_runs` table (one row per job, upserted). `/statusz.last_cron` is the read
surface. A useful alert is:

> `last_cron.stale == true` for more than one scheduler interval

— i.e. milestones have not succeeded in 24h. Wire that probe from deploy-side
(uptime robot, Cloud Scheduler → status key, Watchtower ladder); do **not** add
an alerter into this repo.

## Version env

Terraform sets `IMAGE_VERSION` on the api container to the same pin as the image
tag (`image_version` / `make deploy … VERSION=vX.Y.Z`). `/statusz.version` and
log lines both read it so a fleet registry pin and a live instance can be
cross-checked.
