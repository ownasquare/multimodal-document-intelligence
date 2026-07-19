.DEFAULT_GOAL := help

.PHONY: help sync lock sample demo api worker ui test test-live coverage lint format typecheck security build check

help:
	@echo "sync       Install the locked runtime and development environment"
	@echo "sample     Regenerate the synthetic Northstar PDF"
	@echo "demo       Start the credential-free local workspace"
	@echo "api        Start FastAPI on loopback port 8014"
	@echo "worker     Start the durable ingestion worker"
	@echo "ui         Start Streamlit on loopback port 8514"
	@echo "test       Run deterministic tests without provider calls"
	@echo "test-live  Explicitly run optional live-provider tests"
	@echo "check      Run the complete deterministic quality gate"

sync:
	uv sync --all-groups --frozen

lock:
	uv lock

sample:
	uv run python scripts/generate_sample.py

demo:
	uv run document-intelligence demo

api:
	uv run document-intelligence api

worker:
	uv run document-intelligence worker

ui:
	uv run document-intelligence ui

test:
	uv run pytest -q -m "not live and not e2e" --disable-socket --allow-unix-socket

test-live:
	DOCINTEL_RUN_LIVE_TESTS=1 uv run pytest -q -m live tests/live

coverage:
	uv run pytest -q -m "not live and not e2e" --disable-socket --allow-unix-socket --cov=document_intelligence --cov-branch --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy src

security:
	uv run bandit -q -r src scripts
	uv run pip-audit

build:
	uv build

check: lint typecheck security coverage build
