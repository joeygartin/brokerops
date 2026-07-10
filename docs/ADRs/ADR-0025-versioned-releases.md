# ADR-0025: Versioned releases — a deploy pins a built, tagged image

**Status:** Accepted · **Date:** 2026-07-10

## Context

Until now a deploy built its images from the current working tree
(`make gcp-images CLIENT=x` → `api:latest`/`frontend:latest`, then `terraform
apply` pinning the `:latest` tag). That is fine for a single demo instance rolled
by Cloud Build on every push to main, but it is the wrong foundation for a fleet:

- **No reproducibility.** Two clients deployed an hour apart both pin `:latest`,
  but `:latest` moved in between — so they silently run different, unrecorded code.
  There is no artifact that says "client A is on the exact bits of release X."
- **No rollback target.** Rolling back means rebuilding from an older checkout and
  hoping the result matches; there is no immutable image that *is* the prior release.
- **Working-tree drift.** `make gcp-images` builds whatever is checked out, including
  uncommitted edits. A deploy could ship bits that exist on no commit.

This is the first task in the fleet-ops lane (BOP-031..038); versioned releases are
its foundation — the upgrade driver (BOP-033), parity check (BOP-034), and health
surface (BOP-035) all assume "a client is on a known release."

## Decision

1. **A release is a git tag `vMAJOR.MINOR.PATCH`.** Pushing the tag triggers a
   Cloud Build (`cloudbuild.release.yaml`) that builds the api + frontend images
   **once** and pushes them to Artifact Registry tagged with the version — e.g.
   `api:v0.1.0`. The build does **not** move `:latest` and does **not** deploy.
   Promoting a release to a client is a separate, deliberate step. The registry is a
   trigger substitution (`_AR`), not hardcoded — a fleet can publish releases into a
   shared registry/project.

2. **A deploy pins a version.** `make deploy CLIENT=x VERSION=v0.1.0` passes
   `-var image_version=v0.1.0`; Terraform derives the full image refs from
   `project_id + region + image_version`, so a client's committed tfvars carries only
   the version, never a hand-maintained registry path. `terraform plan` shows the exact
   pinned tag on both Cloud Run services.

3. **The prod path fails without a version.** `make deploy` requires `VERSION` (clear
   error otherwise), and the Terraform `image_version` variable rejects an empty value.
   A prod deploy cannot silently fall back to "whatever `:latest` is now."

4. **Working-tree builds survive behind an explicit, labeled escape hatch.**
   `make deploy-dev CLIENT=x` builds from the current tree, pushes `:latest`, and
   deploys that tag. It is unversioned and unreproducible by construction — for
   iterating on a demo/sandbox instance, never for a client release.

5. **The demo and CD are unchanged in behavior.** The main→demo Cloud Build
   (`cloudbuild.yaml`) still builds `:latest` + `:$SHORT_SHA` on every push and rolls
   the demo by SHA via an image-only `gcloud run deploy` (env/secrets preserved). The
   demo's tfvars pin `image_version = "latest"`, which resolves to the same
   `…/api:latest` ref as before — no drift on the next `terraform apply`. Local
   `docker compose` / `make demo` build from source as always; releases are a
   cloud-deploy concern only.

## Consequences

- (+) Every client instance is traceable to an immutable, git-tagged image built
  once — reproducible deploys and a real rollback target (redeploy the prior `VERSION`).
- (+) Terraform tfvars shrink to a version string; the registry path is derived, so it
  can't drift per client or get copy-pasted wrong.
- (+) The fleet-ops lane has its foundation: "what release is client X on" is now a
  first-class, answerable question.
- (−) Shipping a client change now has an explicit release step (tag → CI green →
  release build → `make deploy VERSION=`) rather than a working-tree push. That
  ceremony is the point for clients; `make deploy-dev` keeps the fast path for sandboxes.
- (−) The tag-push Cloud Build trigger is provisioned out-of-band in GCP (like the
  existing main-CD trigger); `docs/RELEASING.md` documents it. Not expressible in the
  committed repo alone.

The single-engine CI gate (ADR-0019) is the release quality bar — a tag is cut from a
commit that is already green on main.
