.PHONY: setup test lint run

setup:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .

run:
	uv run uvicorn conductor.app:app --reload --port 8000
