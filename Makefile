.PHONY: help install up down migrate seed ingest-fixtures test test-unit test-integration test-security test-fixtures smoke test-llm run-api run-worker run-ui clean

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest

help:
	@echo "Available commands:"
	@echo "  make install         - Install dependencies with uv"
	@echo "  make up              - Start Docker Compose services"
	@echo "  make down            - Stop Docker Compose services"
	@echo "  make migrate         - Run database migrations"
	@echo "  make seed            - Seed staff directory, users, and CRM data"
	@echo "  make ingest-fixtures - Ingest E001-E012 sample enquiries"
	@echo "  make test            - Run all test suites"
	@echo "  make test-fixtures   - Run acceptance fixture test suite"
	@echo "  make test-llm        - Test configured LLM gateway connectivity"
	@echo "  make smoke           - Run API smoke tests"
	@echo "  make run-api         - Start FastAPI backend server"
	@echo "  make run-worker      - Start asynchronous background worker"
	@echo "  make run-ui          - Start Review UI development server"
	@echo "  make clean           - Remove temporary files and caches"

install:
	./bin/uv pip install --python .venv/bin/python -e ".[dev]"

up:
	docker compose up -d

down:
	docker compose down

migrate:
	$(PYTHON) -m db.migrations.runner

seed:
	$(PYTHON) scripts/seed.py

ingest-fixtures:
	$(PYTHON) scripts/ingest_fixtures.py

test:
	$(PYTEST) -v tests/

test-unit:
	$(PYTEST) -v tests/unit/

test-integration:
	$(PYTEST) -v tests/integration/

test-security:
	$(PYTEST) -v tests/security/

test-fixtures:
	$(PYTEST) -v tests/fixtures/

smoke:
	$(PYTHON) scripts/smoke_test.py

test-llm:
	$(PYTHON) scripts/test_llm.py

run-api:
	$(PYTHON) -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

run-worker:
	$(PYTHON) apps/worker/main.py

run-ui:
	cd apps/review-ui && npm run dev

clean:
	rm -rf __pycache__ .pytest_cache .coverage htmlcov *.db .venv/var
	find . -type d -name "__pycache__" -exec rm -rf {} +
