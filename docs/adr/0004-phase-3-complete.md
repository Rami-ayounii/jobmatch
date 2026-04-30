# ADR 0004 — Phase 3: Retrieval + Rerank Complete

**Date:** 2026-04-30
**Status:** Accepted
**Branch:** master

## What was built

- **`POST /jobs`** — JSON body `{title, description_text}`, embeds synchronously via `embed_text`, persists to DB with embedding. Returns `{id, title, status, created_at}`.
- **`GET /jobs/{id}`** — returns job info, 404 if not found or wrong tenant.
- **`CandidateRepo.retrieve_hybrid`** — raw SQL hybrid query: `0.7 * (1 - cosine_distance) + 0.3 * word_similarity`, filtered by tenant, `deleted_at IS NULL`, `embedding IS NOT NULL`, LIMIT 50.
- **`retrieve_candidates` node** — pipeline node that calls `retrieve_hybrid`, sets RLS context, writes `retrieved_candidates` to state.
- **`rerank_candidates` node** — pipeline node that calls BGE reranker, reduces top-50 → top-15 sorted by rerank score.
- **`PipelineState`** expanded with `RetrievedCandidate` and `RankedCandidate` TypedDicts.
- **Migration 0011** — GIN trgm index on `candidates.resume_text` for fast `word_similarity` queries.

## Trade-offs

| Decision | Rationale |
|---|---|
| Synchronous embedding on job create | JD embedding is fast (~200ms); simpler than a Celery task for a single vector |
| Raw SQL `text()` for hybrid query | pgvector `<=>` + `word_similarity` in one SELECT is cleaner as raw SQL than ORM constructs |
| Vector bound as string `[x,y,...]::vector(1536)` | Avoids asyncpg codec registration; reliable with SQLAlchemy `text()` |
| Weights 0.7 / 0.3 (vector / trgm) | Semantic similarity is primary signal; trgm catches keyword matches the embedding may miss |

## What was skipped

- Structured filters (e.g., filter by `years_experience`) — deferred to Phase 4 when `PipelineState` has full job `structured_jsonb`
- Load/latency test (<300ms p95) — needs Docker + real data
- Integration tests require Docker

## Coverage

- `app/pipeline/nodes/retrieve.py` — 100% (contract tests)
- `app/pipeline/nodes/rerank.py` — 100% (contract tests)
- `app/api/jobs.py` — models unit-tested; endpoint tests need Docker

## What's next

**Phase 4** — Pipeline graph + agent:
- Wire all nodes into LangGraph `StateGraph`
- Implement `reason.py` (Claude Sonnet agent, max 6 tool calls)
- Implement all 5 agent tools
- `validate_output` with 8 deterministic checks + citation faithfulness
