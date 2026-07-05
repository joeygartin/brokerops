.PHONY: dev test frontend-test lint demo migrate gcp-bootstrap gcp-images deploy secrets

TF := terraform -chdir=infra

dev:
	docker compose up --build

test:
	uv run pytest

frontend-test:
	cd frontend && npm ci && npm run test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy api/src api/tests core/src core/tests \
		integrations/email_ses/src integrations/email_ses/tests \
		integrations/email_smtp/src integrations/email_smtp/tests \
		integrations/email_stub/src integrations/email_stub/tests \
		integrations/mls_reso/src integrations/mls_reso/tests \
		integrations/followupboss/src integrations/followupboss/tests \
		integrations/google_drive/src integrations/google_drive/tests \
		integrations/google_oidc/src integrations/google_oidc/tests \
		integrations/llm_extraction/src integrations/llm_extraction/tests \
		integrations/pydantic_ai_extraction/src integrations/pydantic_ai_extraction/tests \
		integrations/sierra_crm/src integrations/sierra_crm/tests \
		integrations/vapi/src integrations/vapi/tests \
		orchestration/langgraph/src orchestration/langgraph/tests \
		orchestration/adk/src orchestration/adk/tests

migrate:
	uv run alembic -c api/alembic.ini upgrade head

demo:
	docker compose up --build -d
	@echo "waiting for api…" && sleep 8
	curl -sf -X POST http://localhost:8000/demo/seed | python3 -m json.tool
	@echo "demo ready → frontend http://localhost:5173 | api http://localhost:8000"

# ── GCP deploy (per client) ──────────────────────────────────────────────
# One-time per project: make gcp-bootstrap GCP_PROJECT=… GCP_REGION=… TF_STATE_BUCKET=…
gcp-bootstrap:
	infra/bootstrap/bootstrap.sh $(GCP_PROJECT) $(GCP_REGION) $(TF_STATE_BUCKET)

# Build + push images for a client: make gcp-images CLIENT=demo
gcp-images:
	scripts/build_push_images.sh $(CLIENT)

# Deploy a client: TF_STATE_BUCKET=… make deploy CLIENT=demo
deploy:
	@test -n "$(CLIENT)" || (echo "usage: make deploy CLIENT=<name>"; exit 1)
	@test -n "$(TF_STATE_BUCKET)" || (echo "set TF_STATE_BUCKET (see .env.example)"; exit 1)
	$(TF) init -reconfigure -input=false \
		-backend-config="bucket=$(TF_STATE_BUCKET)" \
		-backend-config="prefix=brokerops/$(CLIENT)"
	$(TF) apply -var-file="clients/$(CLIENT).tfvars"

# Push real integration keys to Secret Manager: make secrets CLIENT=acme
secrets:
	scripts/push_secrets.sh $(CLIENT)
