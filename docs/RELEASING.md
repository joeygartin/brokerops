# Releasing brokerops

A release is an immutable, git-tagged build that clients pin. This is how you cut
one and roll it out. Rationale and rules: [ADR-0025](ADRs/ADR-0025-versioned-releases.md).

## The model in one line

> `git tag vX.Y.Z` → CI green → a release build pushes version-tagged images →
> `make deploy CLIENT=<c> VERSION=vX.Y.Z` pins that exact image.

Working-tree builds (`make deploy-dev`) exist for sandboxes only — never for a client.

## Versioning

Tags are `vMAJOR.MINOR.PATCH` (e.g. `v0.3.1`). Pre-1.0, treat MINOR as the feature
line and PATCH as fixes. The tag name is the image tag, verbatim.

## One-time setup: the release trigger

The release build runs in Cloud Build, triggered on tag push — provisioned in GCP,
like the existing main→demo CD trigger (it is not expressible in the repo alone):

```bash
gcloud builds triggers create github \
  --name=brokerops-release \
  --repo-name=brokerops --repo-owner=joeygartin \
  --tag-pattern='^v.*$' \
  --build-config=cloudbuild.release.yaml \
  --project=brokerops-demo
# To publish releases into a shared fleet registry instead of the demo project,
# add: --substitutions=_AR=<region>-docker.pkg.dev/<project>/brokerops
```

`cloudbuild.release.yaml` builds api + frontend once, pushes `…/api:<tag>` and
`…/frontend:<tag>`, and stops. It does not move `:latest` and does not deploy.

## Cutting a release

1. **Land the change on `main` and confirm CI is green.** The gate is the single
   LangGraph-engine e2e (ADR-0019) plus lint/test, frontend, contract-drift,
   gitleaks, and the Playwright golden path — all in `.github/workflows/ci.yml`. A
   tag is only ever cut from a commit already green on main.

2. **Tag and push:**
   ```bash
   git tag v0.3.1
   git push origin v0.3.1
   ```

3. **Watch the release build.** The `brokerops-release` trigger fires on the tag.
   Confirm both images are in Artifact Registry, built from the tag:
   ```bash
   gcloud artifacts docker tags list \
     us-west1-docker.pkg.dev/brokerops-demo/brokerops/api --project=brokerops-demo
   # expect: v0.3.1
   ```

## Rolling a release to clients

Per client, pin the new version:

```bash
TF_STATE_BUCKET=<client-state-bucket> make deploy CLIENT=<client> VERSION=v0.3.1
```

`terraform plan` shows both Cloud Run services moving to `…:v0.3.1` — verify before
applying. A missing `VERSION` fails fast (the prod path never falls back to `:latest`).

For a **fleet-wide** upgrade — pin every client and verify each rolled — use the
upgrade driver (BOP-033, the follow-on to this task). Until it lands, repeat the
per-client `make deploy … VERSION=` above for each client.

**Rollback** is the same command with the prior tag: `make deploy CLIENT=<client>
VERSION=v0.3.0`. The old image is still in the registry (tags are immutable), so the
rollback ships exactly the prior bits.

## The demo is not a release

The public `brokerops-demo` tracks `main`: every push triggers `cloudbuild.yaml`,
which builds `:latest` + `:$SHORT_SHA` and rolls the demo by SHA (env/secrets
preserved). Its tfvars pin `image_version = "latest"`. The demo deliberately runs
tip-of-main, not a pinned release — see the deploy section of the top-level
`README.md`. To iterate on any instance from an unversioned working tree, use
`make deploy-dev CLIENT=<c>` (builds the tree, pins `:latest`).
