# Offboarding a client

A client leaving is a **documented, tested path**, not an improvisation: export their
data, hand it over, tear down the instance, update the registry. This is also the
honest sales answer to *"what happens to our data if we cancel?"*

Design: `brokerops-deployment-model-v1` §4.6 / FLEET-6. Implementation: BOP-036.
Hand-run (manual autonomy) — same posture as the deploy hardening checklist and the
fleet upgrade driver.

## What we keep vs. what we hand over

| After a successful A1 (hosted) offboard | |
|---|---|
| **We keep** | The fleet-registry line only: opaque slug + `offboarded_at` date (+ non-identifying fields already there). History for status/invoicing. **Nothing else** from that client. |
| **We hand over** | One dated archive: restorable Postgres dump + documents FileRef manifest (CSV/JSON) + audit-ledger export. File *bytes* already live in the client's Google Drive; we only ever held pointers. |
| **We destroy** | The per-client GCP resources Terraform manages (Cloud Run, Cloud SQL, Secret Manager shells, scheduler, SAs, …) in that client's project. Secrets go with the project resources — **verified**, not assumed. |

B-tier is different — see [§ B-tier](#b-tier-client-infra--client-keeps-everything) below.

## Order of operations (A1 / hosted)

```
1. Confirm the leave (commercial + access freeze if needed)
2. Export  →  archive (dump + documents manifest + audit)
3. Deliver →  client-controlled GCS bucket or secure local handoff
4. Secret-scan the archive (automated; fails closed)
5. Confirm-gated terraform destroy for that client
6. Verify Secret Manager has no brokerops-<slug>-* leftovers
7. Mark fleet.yml offboarded_at (entry kept — never deleted)
8. Hand the archive to the client; close the commercial loop
```

Never destroy before the export is delivered and the scan is clean. Never delete the
registry row.

### Command

```bash
# Requires: CLIENT_DATABASE_URL (owner DSN preferred), TF_STATE_BUCKET for destroy.
export CLIENT_DATABASE_URL='postgresql://brokerops:…@/brokerops_demo?host=/cloudsql/…'
export TF_STATE_BUCKET=…   # same bucket make deploy uses

# Preview only
scripts/offboard_client.sh demo --dest ./exports --dry-run

# Full path (prompts before destroy + registry mark)
scripts/offboard_client.sh demo --dest gs://client-handoff-bucket/demo/

# Or via make
make offboard CLIENT=demo DEST=./exports/demo

# Non-interactive (CI / already-confirmed window)
scripts/offboard_client.sh demo --dest ./exports --yes
```

Flags:

| Flag | Effect |
|------|--------|
| `--dry-run` | Print the plan; touch nothing |
| `--export-only` | Build + deliver + scan; skip destroy and registry mark |
| `--skip-destroy` | Skip terraform destroy; **hosted mode still verifies** secrets/Run/SQL clean before mark |
| `--mark-only` | Only set `offboarded_at` in `fleet.yml` (post-teardown recovery; no cloud probes) |
| `--mode client-infra` | B-tier path (no destroy, no A1 cleanup probe) regardless of registry posture |
| `--skip-secrets-verify` | Offline/unit tests only — skip gcloud cleanup probes (never for a real offboard) |
| `--force` | Allow re-run on a slug already marked `offboarded_at` (default: refuse) |

Registry resolution matches the fleet upgrade driver: overlay supplies
`project_id` / `tfvars` when present; otherwise `infra/clients/<slug>.tfvars` and the
`project_id` inside it.

### Export archive layout

```
brokerops-offboard-<slug>-<timestamp>.tar.gz
├── README.md
├── MANIFEST.json
├── database.dump              # pg_restore -d <db> database.dump
├── documents_manifest.csv
├── documents_manifest.json    # FileRefs only — no file bytes
└── audit_ledger.json          # mutation_records (ADR-0010)
```

Restore check:

```bash
pg_restore -l database.dump | head
# or into an empty database:
pg_restore --no-owner --no-acl -d "$SCRATCH_DATABASE_URL" database.dump
```

### Secrets + billable cleanup (verified, not assumed)

After destroy (and on hosted `--skip-destroy` recovery) the driver:

1. Scans **Secret Manager** for `brokerops-<slug>-*` in the deploy project **and** any
   extra projects from `--secret-project` / `OFFBOARD_SECRET_PROJECTS` (covers the rare
   case of secrets that live outside the client project).
2. Lists Cloud Run, Cloud SQL, Cloud Scheduler, and service accounts for the same prefix
   — any hit is a hard failure. The Cloud Scheduler probe region is **derived from the
   client's Terraform config** (the `region` in its tfvars, or Terraform's declared
   default when the tfvars omits it) — the module provisions exactly one job in
   `var.region`, so this is authoritative, not a guess. If the region can't be
   determined (no tfvars), the run **fails closed**; pin it with
   `OFFBOARD_SCHEDULER_LOCATION=<region>` if a job was relocated.
3. Runs `terraform state list` for `brokerops/<slug>` and requires an **empty** state
   (authoritative proof that every resource this module managed is gone).

```bash
# secrets (per project scanned)
gcloud secrets list --project="$PROJECT" --format='value(name)'
# terraform state
terraform -chdir=infra state list   # expect: empty
```

Probe or list failures **fail the offboard** — they never count as "clean".

### Registry mark

`infra/clients/fleet.yml` gains:

```yaml
  - slug: acme
    …
    last_upgraded: 2026-06-01
    offboarded_at: 2026-07-31   # set by the driver; entry retained
```

`make fleet-status` shows `offboarded YYYY-MM-DD`. The upgrade driver skips offboarded
clients. Identifying overlay fields for that slug may be deleted locally (gitignored);
the committed row stays.

## B-tier (client-infra) — client keeps everything

Enterprise posture (ADR-0026 / deployment-model B): the brokerage owns the GCP project.
On cancel:

1. **Export is optional** — they already have the database and Drive. Offer the same
   archive if they want a brokerops-shaped handoff.
2. **Do not `terraform destroy` their project.** `--mode client-infra` (or registry
   `posture: client-infra`) refuses destroy.
3. **Remove our access:**
   - revoke WebDrvn operator IAM on the project,
   - rotate/delete any keys we held,
   - remove our principal from the Terraform state bucket prefix for that slug,
   - drop the identifying overlay entry locally.
4. **Mark** `offboarded_at` in the fleet registry so fleet tooling stops walking them.

```bash
scripts/offboard_client.sh acme --dest ./exports --mode client-infra --skip-export --yes
# then complete the IAM/state ACL teardown from the list above
```

## Demo-client drill (acceptance)

Proves the path end-to-end without improvisation. Use a **scratch** deploy when
touching cloud (never destroy the long-lived public demo without intent).

### Local (export + scan + registry — no cloud destroy)

The unit/integration suite seeds a throwaway Postgres, runs the export builders and
secret scan, and applies `mark_offboarded` to a temp manifest. Run:

```bash
uv run pytest scripts/tests/test_offboard_client.py -q
```

For a hand drill against the compose database:

```bash
docker compose up -d db
# migrate + seed as usual (make demo, or alembic + POST /demo/seed)
export CLIENT_DATABASE_URL=postgresql://brokerops:brokerops@localhost:5432/brokerops_demo
scripts/offboard_client.sh demo --dest /tmp/offboard-demo --export-only
tar -tzf /tmp/offboard-demo/brokerops-offboard-demo-*.tar.gz
# restore smoke:
#   createdb scratch && pg_restore --no-owner -d scratch /tmp/.../database.dump
```

### Cloud scratch (full AC: destroy leaves no billable resources)

```bash
# 1. Scratch project + bootstrap (one-time)
# 2. make deploy-dev CLIENT=demo   # or a dedicated scratch slug/tfvars
# 3. seed
# 4. export CLIENT_DATABASE_URL=… TF_STATE_BUCKET=…
scripts/offboard_client.sh demo --dest gs://$TF_STATE_BUCKET/offboard-drill/ --yes
# 5. Confirm: gcloud run/sql/secrets list show no brokerops-demo-* leftovers
# 6. fleet.yml shows offboarded_at (revert the demo row if this was the shared demo)
```

## Sales one-liner

> Your data lives in a dedicated database and your own Drive. If you cancel we export
> a full dump plus the document pointer list and audit trail, hand them to you, and
> tear down our instance. We keep only a non-identifying registry line that you left
> on date X — not your deals, files, or secrets.
