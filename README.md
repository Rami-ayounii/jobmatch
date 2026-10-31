# JobMatch

Agentic resume matching for recruiters: post a job, get a shortlist of 5 candidates with cited reasons
and a draft invite for each, and nothing reaches a candidate until a recruiter approves it.

## How a match runs

A 9-node LangGraph pipeline (`app/pipeline/`):

1. `validate_inputs` – checks the job and its description
2. `embed_query` – loads the job and its embedding
3. `retrieve_candidates` – hybrid search: pgvector similarity + BM25 (Postgres trigram index)
4. `rerank_candidates` – BGE cross-encoder reranker
5. `enrich_candidates` – parallel re-parse of the top 15 resumes
6. `reason` – a **bounded LLM agent** derives a rubric and builds the shortlist
   (at most 6 tool calls, $1.50 cost cap, 45 s wall clock)
7. `validate_output` – 8 deterministic checks, including **citation faithfulness**:
   every cited reason must be an exact substring of the resume
8. `generate_drafts` – 5 invite e-mails drafted in parallel
9. `persist_results` – one transaction: match, invites and an audit event

The agent's tools are read-only or scope-limited (`get_candidate_profile`, `parse_missing_fields`,
`rescore_candidate`, `request_more_candidates`, `finalize_shortlist`). If it runs out of tool calls,
the top 5 by rerank score are used and the trace says so.

## Safety

- **Human approval gate:** invites are sent only after `POST /invites/{id}/approve` by an authenticated recruiter.
- The send worker aborts unless the invite is `approved` **and** has an approver (checked against the database).
- `send_email` is **not** an agent tool, and a unit test enforces it; adversarial tests cover every non-approved state.
- Candidate e-mails are stored encrypted and decrypted only inside the send transaction.

## Stack

FastAPI · Celery + Redis · PostgreSQL (pgvector, pg_trgm) · SQLAlchemy + Alembic · LangGraph · LiteLLM · BGE reranker ·
pytest (unit, contract, integration, adversarial) · ruff · mypy

## Quick start

```bash
uv sync --extra dev          # dependencies
docker compose up -d         # Postgres + Redis
make migrate                 # database schema
make dev                     # API + Celery worker
curl http://localhost:8000/health
```

Configuration: copy `.env.example` to `.env` and fill in your own keys.

| Command | Description |
|---|---|
| `make dev` | Start uvicorn + Celery worker |
| `make test` | Run pytest with coverage |
| `make lint` | ruff + mypy |
| `make migrate` | alembic upgrade head |

## Build phases

- [x] Phase 0 — Bootstrap
- [x] Phase 1 — Data layer + auth
- [x] Phase 2 — Ingest path
- [x] Phase 3 — Retrieval + rerank
- [x] Phase 4 — Pipeline graph + agent
- [x] Phase 5 — Drafts + human approval gate
- [ ] Phase 6 — Eval harness (gold set, NDCG@5)
- [ ] Phase 7 — Observability + cost telemetry
- [ ] Phase 8 — Polish + first design partner

Design records for each phase: [`docs/adr/`](docs/adr).
