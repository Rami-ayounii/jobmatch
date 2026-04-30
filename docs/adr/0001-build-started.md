# ADR 0001 — Build Started

**Date:** 2026-04-23  
**Status:** Accepted  
**Phase:** 0 — Bootstrap

## What was built

Phase 0 scaffold for JobMatch:

- `pyproject.toml` — all dependencies from CLAUDE.md §3, pinned to major versions
- `docker-compose.yml` — Postgres 16 with pgvector + Redis for local dev
- `app/main.py` — FastAPI app factory; `GET /health` returns `{"status":"ok"}`
- Alembic initialized with `alembic.ini` + `app/db/migrations/env.py`; one empty baseline migration
- `Makefile` — `make dev`, `make test`, `make eval`, `make lint`, `make migrate`, `make seed`
- `.env.example` — all env vars from §11
- Full directory skeleton matching §5

## Trade-offs

- Used `uv` as the package manager (faster than poetry, compatible with `pyproject.toml`)
- Celery chosen over Arq (more documentation, wider ecosystem)
- Alembic migration folder placed at `app/db/migrations/` (not root) to keep DB logic co-located

## Skipped / stubbed

- No real DB models yet (Phase 1)
- No auth wiring (Phase 1)
- `make seed` is a no-op stub until Phase 2
- `make eval` prints a placeholder until Phase 6

## What's next

Phase 1 — Data layer + auth:
- All 7 tables via Alembic migrations
- Postgres RLS policies
- Clerk JWT validation
- pgcrypto encryption
