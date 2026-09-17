# Convenience targets for Unix-like shells and CI.
# Windows developers should use the equivalent scripts in scripts/*.ps1.

PYTHON ?= python

.PHONY: help bootstrap format lint typecheck test verify clean

help:
	@echo "bootstrap  - create .venv and install dev dependencies"
	@echo "format     - auto-format the codebase"
	@echo "lint       - run ruff lint checks"
	@echo "typecheck  - run mypy in strict mode over src/"
	@echo "test       - run pytest with coverage"
	@echo "verify     - SDLC Step 7: run every quality gate (CI parity)"
	@echo "clean      - remove caches and build artefacts"

bootstrap:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"
	.venv/bin/python -m pre_commit install

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check . --fix

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m pytest --cov=src --cov-report=term-missing

verify: lint typecheck test
	@echo "All gates passed. Next: SDLC Step 8 - documentation drift audit."

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -exec rm -rf {} +
