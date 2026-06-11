# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 6** — per-client GCP deploys via Terraform: `make deploy
> CLIENT=acme` stands up two Cloud Run services, a Cloud SQL database, Secret
> Manager shells (keys pushed out-of-band by `make secrets`), and the milestone-cron
> Scheduler job, with least-privilege service accounts. A demo client deploys fully
> self-contained: the MLS/CRM/voice integrations run their bundled stubs in-process
> via the `internal` sentinel — zero external credentials, even in the cloud. Plus
> Phases 1–5: mock RESO Web API, durable HITL workflows, FollowUpBoss integration,
> scheduled transaction coordination, voice follow-up with structured extraction.

## Quick start (demo mode — zero credentials required)

```bash
docker compose up
```

- API: <http://localhost:8000/healthz>
- Frontend: <http://localhost:5173>

## Development

```bash
uv sync --all-packages   # install workspace deps
make test                # unit tests
make lint                # ruff check + format check
```

## Deploying a client to GCP

```bash
# one-time per GCP project
make gcp-bootstrap GCP_PROJECT=<id> GCP_REGION=us-west1 TF_STATE_BUCKET=<bucket>

# per client
cp infra/clients/_template.tfvars infra/clients/acme.tfvars   # edit (no secrets)
make gcp-images CLIENT=acme                                   # build + push images
TF_STATE_BUCKET=<bucket> make deploy CLIENT=acme              # terraform apply
make secrets CLIENT=acme                                      # push real API keys
```

`infra/clients/demo.tfvars` deploys the self-contained demo (in-process stubs,
no secrets). Terraform state lives in GCS; tfvars are committed and contain no
secret values.

## License

Apache-2.0 — see [LICENSE](LICENSE).
