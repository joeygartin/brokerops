# ADR-0003: Frontend serves the API same-origin via an nginx proxy

**Status:** Accepted · **Date:** 2026-06-11

## Context

The frontend bundle needs to know the API's URL, and the API's CORS policy needs to
know the frontend's origin — but on Cloud Run, neither URL exists until its service
is created, and the frontend URL bake happens even earlier, at image build time.

The first design predicted Cloud Run's deterministic URL format
(`https://<service>-<project#>.<region>.run.app`) to break the cycle. In practice
the prediction failed on the first real deploy: the created services routed only on
the legacy random-suffix URL format. Predicting Google's URL scheme is fragile by
construction — it couples the deploy to an undocumented-enough behavior.

A second, subtler deploy lesson recorded here because it shaped the same surface:
**Google's frontend reserves `/healthz` on `*.run.app` URLs** and intercepts it with
a generic 404 before the request reaches the container. External health checks must
use a different path (`/readyz` here).

## Decision

Stop predicting URLs entirely:

1. The frontend bundle calls the API at the **relative path `/api`**
   (`VITE_API_BASE=/api`, baked at image build). One frontend image works for every
   client and environment.
2. The frontend's nginx proxies `/api/*` to the API service. The upstream URL is
   injected at deploy time as the `API_UPSTREAM` env var, rendered into nginx config
   by the stock image's envsubst — Terraform passes the API service's **real**
   `.uri` attribute, available because the frontend is created after the API.
3. Terraform references real `.uri` attributes everywhere a service URL is needed
   (Cloud Scheduler target, outputs). Nothing computes a URL it hopes will exist.

## Consequences

- (+) No build-time URL coupling: one frontend image per release, not per client.
- (+) Browser traffic is same-origin in the cloud — CORS configuration becomes a
  local-dev concern only.
- (+) The deploy has no fragile assumptions about Google's URL formats.
- (−) One extra hop (nginx → API) on browser API calls; negligible at this scale.
- (−) Local dev keeps the direct-URL path (`VITE_API_BASE` defaults to
  `http://localhost:8000`), so the dev and prod request paths differ slightly —
  documented in DEMO.md.
