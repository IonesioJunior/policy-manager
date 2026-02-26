.PHONY: lint check tests

lint:
	uv run ruff check --fix src tests
	uv run ruff format src tests

check:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src

tests:
	uv run pytest
