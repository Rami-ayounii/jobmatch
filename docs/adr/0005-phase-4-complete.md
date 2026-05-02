# ADR 0005 — Phase 4: Pipeline Graph + Agent Complete

**Date:** 2026-05-02
**Status:** Accepted
**Branch:** master

## What was built

The full LangGraph pipeline (9 nodes) and bounded Claude Sonnet agent are implemented and tested.

**Nodes implemented:**
- `validate_inputs` — UUID + job_description presence checks
- `embed_query` — loads job row + embedding from DB
- `retrieve_candidates` — hybrid pgvector + BM25 query (Phase 3)
- `rerank_candidates` — BGE reranker singleton (Phase 3)
- `enrich_candidates` — parallel Groq re-parse of top-15
- `reason` (agent node) — rubric derivation + bounded Claude Sonnet loop (max 6 tool calls, $1.50 cap, 45s wall-clock)
- `validate_output` — 8 deterministic checks including citation faithfulness
- `generate_drafts` — 5 parallel Groq invite email generations
- `persist_results` — single DB transaction: match update + invite rows + audit event

**Agent tools (all read-only/scope-limited):**
- `get_candidate_profile` — reads enriched pool in-state (no DB)
- `parse_missing_fields` — Groq re-parse for specific fields
- `rescore_candidate` — records score trace (no mutation)
- `request_more_candidates` — returns from reranked pool not yet enriched
- `finalize_shortlist` — submits exactly 5 candidates to end the loop

**API:**
- `POST /matches/run` — validates job exists, creates match row, dispatches Celery task
- `GET /matches/{run_id}` — returns status, shortlist, cost

## Trade-offs made

- **Node name `generate_drafts` instead of `draft_invites`**: LangGraph prohibits node names that collide with `PipelineState` keys. `draft_invites` is a state key, so the node was renamed to `generate_drafts`. The function name is still `draft_invites`.
- **Citation faithfulness uses exact substring match**: simple and auditable; no fuzzy matching. If the LLM paraphrases a citation, it fails — which is the correct behavior for the EU AI Act high-risk context.
- **Fallback shortlist**: if the agent loop exhausts tool calls without calling `finalize_shortlist`, the top-5 by rerank score are used. This prevents stuck pipelines at the cost of reduced auditability (trace shows `fallback_shortlist`).
- **`_extract_cost` fallback**: if `litellm.completion_cost` fails (mocked in tests), a rough token-based estimate is used. Real runs use the litellm calculation.

## What was skipped / stubbed

- **`job_description` field in PipelineState**: populated from DB in `embed_query`; the `validate_inputs` node checks for it as a non-empty string, but in production the API handler pre-populates via the Celery task.
- **Logfire spans per node**: not yet wired — Phase 7 handles observability.
- **Sentry run_id tagging**: Phase 7.
- **LiteLLM cost callback to `audit_events`**: Phase 7.

## What's next

Phase 5: draft + human approval gate (`POST /invites/{id}/approve`, `POST /invites/{id}/reject`, `send_worker`).
