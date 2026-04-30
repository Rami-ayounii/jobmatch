# JobMatch

Agentic HR resume-matching SaaS — Phase 0 bootstrap.

## Quick start

```bash
# Install dependencies
uv sync --extra dev

# Start Postgres + Redis
docker compose up -d

# Run migrations
make migrate

# Start API + worker
make dev
```

## Health check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Commands

| Command | Description |
|---|---|
| `make dev` | Start uvicorn + celery worker |
| `make test` | Run pytest with coverage |
| `make eval` | Run gold-set evals (Phase 6) |
| `make lint` | ruff + mypy |
| `make migrate` | alembic upgrade head |
| `make seed` | Load test data (Phase 2) |

## Build phases

- [x] Phase 0 — Bootstrap
- [ ] Phase 1 — Data layer + auth
- [ ] Phase 2 — Ingest path
- [ ] Phase 3 — Retrieval + rerank
- [ ] Phase 4 — Pipeline graph + agent
- [ ] Phase 5 — Drafts + human approval gate
- [ ] Phase 6 — Eval harness
- [ ] Phase 7 — Observability + cost telemetry
- [ ] Phase 8 — Polish + first design partner
