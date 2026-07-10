# Fleet registry

The fleet registry answers "which client instances exist, on what version, with which
onboarding steps done?" — the questions the upgrade driver (BOP-033) walks and the
status/invoicing questions read. It is a **file + scripts**, deliberately: no database,
no service.

## Two files, one keyed by slug

This is a public case-study repo, so the registry is split so that **no
client-identifying value is ever committed** (deployment-model §7.3, ruled 2026-07-05):

| File | Committed? | Holds |
|------|-----------|-------|
| `infra/clients/fleet.yml` | **yes** | opaque/public fields only |
| `infra/clients/fleet-overlay.local.yml` | **no** (gitignored) | the identifying fields |

Both are keyed by the same opaque **slug**. `make fleet-status` reads the committed
manifest and, when the overlay is present, merges its display name / project for a
human-readable table; with no overlay it degrades to slug-only. The upgrade driver and
deploy path read the overlay's identifying fields at runtime.

## Committed manifest schema (`fleet.yml`)

```yaml
clients:
  - slug: demo            # opaque lowercase token [a-z0-9-] — NOT the brokerage name
    version: latest       # pinned release "vX.Y.Z", or "latest" for the CD-tracked demo
    posture: hosted       # hosted | client-infra
    billing_model: flat   # flat | tiered — enum ONLY, never an amount
    last_upgraded: 2026-07-08
    onboarding:           # booleans only — the flag, never the underlying value
      ses_identity: true  #   SES domain identity verified
      sandbox_exit: true  #   SES production access (out of sandbox)
      ten_dlc: false      #   10DLC / A2P SMS registration approved
      drive_sa: false     #   Google Drive service account provisioned
      crm_keys: false     #   CRM API keys pushed to Secret Manager
```

`billing_model` records *which posture* applies (the design's Q2: flat-monthly is the
default, tiered stays an option) — the *amount* is identifying and lives in the overlay.

### The committed file cannot hold an identifier

`scripts/fleet.py` enforces this two ways, so a future edit can't leak an identity:

- The entry model is `extra="forbid"` — any field not in the schema above is rejected.
- The known-identifying names (`display_name`, `project_id`, `tfvars`, fee/amount keys,
  `invoice_id`, …) are rejected **by name** with a message pointing at the overlay.

`slug` is additionally required to be an opaque `[a-z0-9-]` token so a brokerage name
can't be smuggled in as the key.

## Gitignored overlay schema (`fleet-overlay.local.yml`)

A mapping keyed by slug. Every field is optional — provide what you have.

```yaml
acme-realty:                        # the same slug as the manifest entry
  display_name: "Acme Realty Group" # example placeholder — real values stay local
  project_id: "brokerops-acme"
  tfvars: "infra/clients/acme.tfvars"
  billing_amount: "$499/mo"         # the amount behind billing_model: flat
  invoice_id: "CUST-0001"
```

Its path is in `.gitignore`; it never leaves the machine.

## Usage

```
make fleet-status                     # render the table (+ overlay if present)
uv run python scripts/fleet.py validate  # validate the committed manifest, nonzero on error
```

`fleet-status` flags **version drift** (a pinned entry behind the latest `vX.Y.Z` git
tag; `latest`-tracking entries show "tracks latest"; before the first release, "no
releases") and **incomplete onboarding** (a `⚠` next to any entry below 5/5).
