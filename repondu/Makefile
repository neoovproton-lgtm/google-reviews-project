.PHONY: install test test-fast lint format run

install:
	uv venv --python 3.12 .venv
	uv pip install -e ".[dev]"
	.venv/bin/playwright install chromium

test:
	.venv/bin/pytest

test-fast:
	.venv/bin/pytest -m "not browser"

lint:
	.venv/bin/ruff check app tests
	.venv/bin/ruff format --check app tests

format:
	.venv/bin/ruff check --fix app tests
	.venv/bin/ruff format app tests

run:
	.venv/bin/uvicorn app.main:app --reload --port 8000
