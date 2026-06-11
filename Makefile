.PHONY: dev test lint demo

dev:
	docker compose up --build

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy api/src api/tests

demo: dev
