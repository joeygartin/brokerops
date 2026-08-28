.PHONY: dev test frontend-test lint generate demo migrate fleet-status fleet-upgrade offboard gcp-bootstrap gcp-images deploy deploy-dev secrets shadow-parity

TF := terraform -chdir=infra

dev:
	docker compose up --build

test:
	uv run pytest

# BOP-043 shadow-parity report against committed fixtures. No client writes.
shadow-parity:
	uv run python scripts/shadow_parity.py \
		core/tests/fixtures/shadow_parity_actuals.json \
		core/tests/fixtures/shadow_parity_snapshot.json

frontend-test:
	cd frontend && npm ci && npm run test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy api/src api/tests core/src core/tests \
		integrations/email_sendgrid/src integrations/email_sendgrid/tests \
		integrations/email_ses/src integrations/email_ses/tests \
		integrations/email_smtp/src integrations/email_smtp/tests \
		integrations/email_stub/src integrations/email_stub/tests \
		integrations/mls_reso/src integrations/mls_reso/tests \
		integrations/followupboss/src integrations/followupboss/tests \
		integrations/google_drive/src integrations/google_drive/tests \
		integrations/google_oidc/src integrations/google_oidc/tests \
		integrations/llm_extraction/src integrations/llm_extraction/tests \
		integrations/pydantic_ai_drafting/src integrations/pydantic_ai_drafting/tests \
		integrations/pydantic_ai_extraction/src integrations/pydantic_ai_extraction/tests \
		integrations/sierra_crm/src integrations/sierra_crm/tests \
		integrations/twilio_sms/src integrations/twilio_sms/tests \
		integrations/vapi/src integrations/vapi/tests \
		orchestration/langgraph/src orchestration/langgraph/tests

# Regenerate the API contract artifacts after any backend wire-shape change:
# export the OpenAPI spec from the Pydantic models, then regenerate the TS
# client from it. Both outputs are committed; CI re-runs this and fails on any
# diff (ADR-0018).
generate:
	uv run python scripts/export_openapi.py
	cd frontend && npm run generate

migrate:
	uv run alembic -c api/alembic.ini upgrade head

demo:
	docker compose up --build -d
	@echo "waiting for api…" && sleep 8
	curl -sf -X POST http://localhost:8000/demo/seed | python3 -m json.tool
	@echo "demo ready → frontend http://localhost:5173 | api http://localhost:8000"

# Render the fleet registry: which clients exist, on what version, onboarding done.
# Merges the gitignored overlay (display name/project) when present. See BOP-032.
fleet-status:
	uv run python scripts/fleet.py status

# Upgrade the fleet to a pinned release: plan → apply → verify per client, stop on the
# first failure (BOP-033). VERSION is required; CLIENT filters to one slug; FLEET_ARGS
# passes flags (e.g. --dry-run, --yes). Needs TF_STATE_BUCKET for a real run/plans.
#   make fleet-upgrade VERSION=v0.2.0                      # every client, confirm each
#   make fleet-upgrade VERSION=v0.2.0 CLIENT=demo FLEET_ARGS=--yes
#   make fleet-upgrade VERSION=v0.2.0 FLEET_ARGS=--dry-run # plans/table only
fleet-upgrade:
	@test -n "$(VERSION)" || (echo "usage: make fleet-upgrade VERSION=vX.Y.Z [CLIENT=<slug>] [FLEET_ARGS=--dry-run]"; exit 1)
	uv run python scripts/fleet_upgrade.py $(VERSION) $(if $(CLIENT),--client $(CLIENT),) $(FLEET_ARGS)

# Offboard a client: export → deliver → secret-scan → confirm-gated destroy → registry mark
# (BOP-036). CLIENT and DEST required. OFFBOARD_ARGS passes flags (--dry-run, --yes,
# --export-only, --mode client-infra, …). Needs CLIENT_DATABASE_URL for export and
# TF_STATE_BUCKET for destroy. See docs/OFFBOARDING.md.
#   make offboard CLIENT=demo DEST=./exports/demo
#   make offboard CLIENT=demo DEST=./exports OFFBOARD_ARGS='--dry-run'
#   make offboard CLIENT=demo DEST=gs://bucket/offboard/ OFFBOARD_ARGS='--yes'
offboard:
	@test -n "$(CLIENT)" || (echo "usage: make offboard CLIENT=<slug> DEST=<path|gs://…> [OFFBOARD_ARGS=--dry-run]"; exit 1)
	@test -n "$(DEST)" || (echo "usage: make offboard CLIENT=<slug> DEST=<path|gs://…> [OFFBOARD_ARGS=--dry-run]"; exit 1)
	scripts/offboard_client.sh $(CLIENT) --dest $(DEST) $(OFFBOARD_ARGS)

# ── GCP deploy (per client) ──────────────────────────────────────────────
# One-time per project: make gcp-bootstrap GCP_PROJECT=… GCP_REGION=… TF_STATE_BUCKET=…
gcp-bootstrap:
	infra/bootstrap/bootstrap.sh $(GCP_PROJECT) $(GCP_REGION) $(TF_STATE_BUCKET)

# Build + push images for a client: make gcp-images CLIENT=demo
gcp-images:
	scripts/build_push_images.sh $(CLIENT)

# Deploy a client at a pinned release (ADR-0025):
#   TF_STATE_BUCKET=… make deploy CLIENT=acme VERSION=v0.1.0
# VERSION is required — a prod deploy must reference a built, tagged release.
# Its images (built once per tag by cloudbuild.release.yaml) must already be in
# Artifact Registry. For a throwaway working-tree build, use `make deploy-dev`.
deploy:
	@test -n "$(CLIENT)" || (echo "usage: make deploy CLIENT=<name> VERSION=vX.Y.Z"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "set TF_STATE_BUCKET (see .env.example)"; exit 1)
	@test -n "$(VERSION)" || (echo "set VERSION=vX.Y.Z (a tagged release) — or use 'make deploy-dev CLIENT=$(CLIENT)' for a working-tree build"; exit 1)
	$(TF) init -reconfigure -input=false \
		-backend-config="bucket=$(TF_STATE_BUCKET)" \
		-backend-config="prefix=brokerops/$(CLIENT)"
	$(TF) apply -var-file="clients/$(CLIENT).tfvars" -var "image_version=$(VERSION)"

# DEV ONLY — build the images from the current working tree, push them as
# `:latest`, and deploy that tag. Unversioned and unreproducible: for iterating
# on a demo/sandbox instance, never for a client release. TF_STATE_BUCKET=… make deploy-dev CLIENT=demo
deploy-dev:
	@test -n "$(CLIENT)" || (echo "usage: make deploy-dev CLIENT=<name>"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "set TF_STATE_BUCKET (see .env.example)"; exit 1)
	scripts/build_push_images.sh $(CLIENT)
	$(TF) init -reconfigure -input=false \
		-backend-config="bucket=$(TF_STATE_BUCKET)" \
		-backend-config="prefix=brokerops/$(CLIENT)"
	$(TF) apply -var-file="clients/$(CLIENT).tfvars" -var "image_version=latest"

# Push real integration keys to Secret Manager: make secrets CLIENT=acme
secrets:
	scripts/push_secrets.sh $(CLIENT)
