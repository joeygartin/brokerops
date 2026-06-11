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
		orchestration/langgraph/src orchestration/langgraph/tests

migrate:
	uv run alembic -c api/alembic.ini upgrade head

demo: dev
