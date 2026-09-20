.PHONY: install test lint format typecheck check sync serve clean

install:
	uv sync
test:
	uv run pytest -q
lint:
	uv run ruff check .
format:
	uv run ruff format .
	uv run ruff check --fix .
typecheck:
	uv run mypy
check: lint typecheck test
sync:
	uv run cfm-cli sync
serve:
	uv run radar-cfm-mcp
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
