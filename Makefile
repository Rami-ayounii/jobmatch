.PHONY: dev test eval lint migrate seed install

install:
	uv sync --extra dev

dev:
	@echo "Starting API server and Celery worker..."
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
	celery -A app.workers.celery_app worker --loglevel=info -c 2

test:
	pytest tests/ -v --cov=app --cov-report=term-missing

eval:
	python -m app.eval.runner

lint:
	ruff check app/ tests/
	mypy app/

migrate:
	alembic upgrade head

seed:
	@echo "Seed script not yet implemented (Phase 2)."
