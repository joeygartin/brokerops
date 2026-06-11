.PHONY: dev test lint demo migrate

dev:
	docker compose up --build

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy api/src api/tests core/src core/tests \
		integrations/mls_reso/src integrations/mls_reso/tests \
		integrations/followupboss/src integrations/followupboss/tests \
		integrations/vapi/src integrations/vapi/tests \
		orchestration/langgraph/src orchestration/langgraph/tests

migrate:
	uv run alembic -c api/alembic.ini upgrade head

demo:
	docker compose up --build -d
	@echo "waiting for api…" && sleep 8
	curl -sf -X POST http://localhost:8000/demo/seed | python3 -m json.tool
	@echo "demo ready → frontend http://localhost:5173 | api http://localhost:8000"
