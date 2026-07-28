.PHONY: help install dev test test-cov lint format type check run clean privacy-install privacy-run

# Makefile for neironir. Targets map to the canonical uv commands.
# On Windows without `make`, use the equivalent `uv run ...` form
# shown in ``@echo`` lines.

PY    ?= python
UV    ?= uv
HOST  ?= 127.0.0.1
PORT  ?= 8000

help:
	@echo "neironir make targets"
	@echo ""
	@echo "  install       uv sync (backend deps)"
	@echo "  dev           uvicorn --reload on $(HOST):$(PORT)"
	@echo "  test          pytest"
	@echo "  test-cov      pytest with coverage report (term-missing)"
	@echo "  lint          ruff check ."
	@echo "  format        ruff format ."
	@echo "  type          mypy backend/neironir"
	@echo "  check         lint + type + test"
	@echo "  run           uvicorn (no reload) on $(HOST):$(PORT)"
	@echo "  clean         drop caches and storage/jobs/*"
	@echo "  privacy-install  pip install -e ./privacy-filter (optional, for real model)"
	@echo "  privacy-run   run uvicorn with NEIRONIR_PRIVACY_FILTER_MODE=subprocess"

install:
	$(UV) sync

dev:
	$(UV) run uvicorn neironir.main:app --reload --host $(HOST) --port $(PORT)

test:
	$(UV) run pytest

test-cov:
	$(UV) run pytest --cov=backend/neironir --cov-report=term-missing

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

type:
	$(UV) run mypy backend/neironir

check: lint type test

run:
	$(UV) run uvicorn neironir.main:app --host $(HOST) --port $(PORT)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	rm -rf storage/jobs/*

privacy-install:
	$(PY) -m pip install -e ./privacy-filter

privacy-run:
	NEIRONIR_PRIVACY_FILTER_MODE=subprocess $(UV) run uvicorn neironir.main:app --host $(HOST) --port $(PORT)
