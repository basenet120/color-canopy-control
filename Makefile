.PHONY: setup test lint run

setup:
	uv sync --frozen --all-extras

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

run:
	uv run uvicorn conductor.app:app --reload --port 8000
