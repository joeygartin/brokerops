# Deployment hardening verification (per client)

How to prove, for any client deploy, that the two capability-security boundaries below
the agent are actually in force:

1. the application connects with a **non-superuser, non-owner** DB role and RLS is
   **enforced** for it, and
2. the client's **service account cannot read another tenant's secrets**.

Context and rationale: [ADR-0021](ADRs/ADR-0021-least-privilege-runtime-db-role.md) (DB
role), [ADR-0012](ADRs/ADR-0012-tenant-scoping.md) (tenant scoping). This is a manual,
cloud-state checklist — the guarantees are not observable from the test suite.

Set once:

```bash
CLIENT=acme                     # the client name
PROJECT=your-gcp-project-id     # that client's OWN GCP project
REGION=us-west1
```

---

## A. The runtime DB role is non-superuser, non-owner, and RLS binds

Open a psql session **as the runtime role** against the client's Cloud SQL instance
(Cloud SQL Auth Proxy or `gcloud sql connect`, logging in as `brokerops_app` with the
password from the `brokerops-<client>-app-database-url` secret):

```sql
-- 1. The runtime role is NOT a superuser and does NOT bypass RLS.
SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
--   expect: f | f

-- 2. It is NOT the owner of the app tables (owner is brokerops).
SELECT tableowner FROM pg_tables WHERE tablename = 'transactions';
--   expect: brokerops   (NOT brokerops_app)

-- 3. RLS is enabled AND forced, with the isolation policy, on EVERY tenant-scoped
--    table — derived from the catalog (every table carrying tenant_id), never a
--    hard-coded list that can drift as new tables are added. Expect ZERO rows: any
--    row returned is a tenant-scoped table missing part of the RLS belt.
SELECT c.relname,
       c.relrowsecurity      AS rls_enabled,
       c.relforcerowsecurity AS rls_forced,
       EXISTS (SELECT 1 FROM pg_policies p
               WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation') AS has_policy
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
WHERE c.relkind = 'r'
  AND EXISTS (SELECT 1 FROM information_schema.columns col
              WHERE col.table_schema = 'public' AND col.table_name = c.relname
                AND col.column_name = 'tenant_id')
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity
           AND EXISTS (SELECT 1 FROM pg_policies p
                       WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation'));
--   expect: (0 rows)

-- 4. RLS binds — a POSITIVE proof, not a coincidental double-zero. Write a probe
--    row under tenant A (as the runtime role, so the write path is exercised),
--    then confirm tenant B cannot see it and tenant A can, and clean up.
SELECT set_config('app.brokerops_tenant', 'bop013-probe-A', false);
INSERT INTO transactions (id, tenant_id, listing_key, stage, parties, contract_date)
VALUES ('_bop013_probe', 'bop013-probe-A', 'L0', 'under_contract', '{}', '2026-01-01');
SELECT count(*) FROM transactions WHERE id = '_bop013_probe';   -- expect 1 (own tenant sees it)
SELECT set_config('app.brokerops_tenant', 'bop013-probe-B', false);
SELECT count(*) FROM transactions WHERE id = '_bop013_probe';   -- expect 0 (RLS hides it cross-tenant)
SELECT set_config('app.brokerops_tenant', 'bop013-probe-A', false);
DELETE FROM transactions WHERE id = '_bop013_probe';            -- clean up (own tenant can)

-- 5. The runtime role cannot weaken or escape the belt (all must ERROR).
ALTER TABLE transactions NO FORCE ROW LEVEL SECURITY;   -- ERROR: must be owner
CREATE TABLE _probe (x int);                            -- ERROR: permission denied for schema public
DROP TABLE transactions;                                -- ERROR: must be owner

-- 6. DML still works (the app must function): step 4's INSERT/SELECT/DELETE under
--    the bound tenant all succeeded as the runtime role.
```

All six must hold. Step 4 is the load-bearing one — it proves the policy is *evaluated
and filters* for the runtime role (the tenant-B count is 0 because RLS hid a row that
demonstrably exists, not because the table is empty).

Confirm the app actually uses the split at runtime:

```bash
gcloud run services describe brokerops-${CLIENT}-api --project ${PROJECT} --region ${REGION} \
  --format='value(spec.template.spec.containers[0].env)' | tr ',' '\n' | grep -E 'DATABASE_URL'
#   expect DATABASE_URL -> ...-app-database-url  and  MIGRATION_DATABASE_URL -> ...-database-url
```

## B. The service account cannot read another tenant's secrets

Each client is its **own GCP project**, so its api service account
(`brokerops-<client>-api@<project>.iam.gserviceaccount.com`) has no bindings in any other
project. Verify the api SA holds `secretAccessor` **only** on this client's secrets and
has no project-wide secret access:

```bash
SA="brokerops-${CLIENT}-api@${PROJECT}.iam.gserviceaccount.com"

# Project-level roles for the SA: expect roles/cloudsql.client ONLY (no secret roles).
gcloud projects get-iam-policy ${PROJECT} \
  --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:${SA}" \
  --format='value(bindings.role)'
#   expect: roles/cloudsql.client   (and nothing granting secret or storage access)

# Per-secret accessor: present on this client's own secrets…
gcloud secrets get-iam-policy brokerops-${CLIENT}-database-url --project ${PROJECT} \
  --format='value(bindings.members)' | grep ${SA}     # -> found

# …and, from this SA's credentials, reading a DIFFERENT project's secret is denied.
# (Run as the SA, e.g. via impersonation.) Expect PERMISSION_DENIED:
gcloud secrets versions access latest --secret=brokerops-other-database-url \
  --project other-clients-project --impersonate-service-account=${SA}
#   expect: PERMISSION_DENIED
```

`roles/cloudsql.client` is the Cloud SQL connector permission only — it does not grant
reading another database's data; data access is still gated by the DB user + RLS from
section A.

---

## Migration path for an existing deploy (single-role → split-role)

Older deploys ran the app as the owner `brokerops` role with a single `DATABASE_URL`.
Terraform + migration 0010 add the split with no manual secret handling:

1. **Pull the change** (this repo at ADR-0021 or later) and rebuild the api image so the
   container carries migration 0010 and the `MIGRATION_DATABASE_URL`-aware `env.py`:
   `make gcp-images CLIENT=<client>` (or rebuild just the api).
2. **Apply Terraform** for the client. It creates the `brokerops_app` Cloud SQL user
   (generated password) and the `brokerops-<client>-app-database-url` secret, and rewires
   the api service so `DATABASE_URL` → the runtime DSN and `MIGRATION_DATABASE_URL` → the
   owner DSN. (For the demo, use the canonical deploy command with its `-var` overrides.)
3. **Roll a new api revision** if the image tag is unchanged (Terraform pins `:latest`, so
   a same-tag push does not roll a revision):
   `gcloud run deploy brokerops-<client>-api --image .../api:latest --region <region> --project <project>`.
   On boot the container runs `alembic upgrade head` as the **owner** role (grants DML to
   `brokerops_app`), then uvicorn connects the domain stores as `brokerops_app`.
4. **Verify** with sections A and B above.

No data migration and no downtime beyond the normal Cloud Run revision cutover: the
tables, RLS policy, and rows are untouched — only the connecting role changes. To roll
back, point `DATABASE_URL` back at the owner `...-database-url` secret and redeploy; the
grants are harmless if left in place.

**Local / compose / CI** need none of this: `MIGRATION_DATABASE_URL` stays unset, the
single role does both jobs, and migration 0010 is a no-op because `brokerops_app` does
not exist — the zero-credential demo is unchanged.
