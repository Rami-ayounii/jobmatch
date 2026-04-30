# JobMatch — Build Brief for Claude Code

This file is the canonical build brief. Read it on every turn. Reference docs in this folder for depth: `ARCHITECTURE_V2.md`, `AGENTIC_WORKFLOW.md`, `USER_CASE_WALKTHROUGH.md`.

If anything in this file conflicts with those docs, **this file wins** — it is the latest decision.

---

## 1. Mission (one paragraph)

JobMatch is an agentic HR resume-matching SaaS for SMB and mid-market companies in the EU/US. An HR user uploads a job description; the system retrieves and ranks the top 5 candidates from the company's resume pool with cited justifications, drafts personalized interview-invite emails, and queues them for human approval before sending. Built solo, $0 budget, 12-month MVP runway. Will be commercialized; not an academic project.

## 2. Hard constraints (do not violate)

- **Solo developer, no team.** Choose simple, well-documented tools. No Kubernetes, no microservices, no custom ML training in v1.
- **Target $500/mo total infra+LLM at 20 paying pilots.** If a design choice can't fit that envelope, reject it.
- **EU AI Act high-risk category** (recruitment AI). All decisions must be auditable and human-supervised. Autonomous email send is FORBIDDEN in v1.
- **GDPR.** PII (candidate names, emails, addresses, raw resume text) must be tenant-isolated, encrypted at rest, deletable within 30 days on request.
- **Multi-tenant from day one.** Every query must be tenant-scoped. Cross-tenant leak = product-ending bug.
- **Python 3.12 only.** Async-first.

## 3. Tech stack (final, no debate)

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Async/await throughout |
| API framework | FastAPI | + Pydantic v2 |
| ORM | SQLAlchemy 2.0 (async) | + Alembic migrations |
| DB | PostgreSQL 15+ with `pgvector` and `pg_trgm` | Single source of truth |
| Vector store | **pgvector inside Postgres** | Do NOT add ChromaDB, Qdrant, Pinecone, Cosmos DB, or Azure AI Search |
| Queue | Celery + Redis | Or Arq if Celery feels heavy. Pick one and stay. |
| Agent orchestration | **LangGraph** | StateGraph, not free-form loops |
| LLM router | **LiteLLM** | All LLM calls through this. No direct SDK calls. |
| Reasoner LLM | `anthropic/claude-sonnet-4.6` (or latest Sonnet) | Fallback: `openai/gpt-4o` |
| Parser/draft LLM | `groq/llama-3.1-8b-instant` | Fallback: `openai/gpt-4o-mini` |
| Embeddings | `openai/text-embedding-3-small` (1536-d) | Fallback: self-hosted BGE small |
| Re-ranker | `BAAI/bge-reranker-base` (local CPU) | Via `sentence-transformers` |
| Email | Resend | Webhook-driven status updates |
| Auth | Clerk | Do not roll your own |
| Object storage | Cloudflare R2 (S3-compatible) | For raw resume files |
| Observability | Sentry + Logfire (Pydantic) | Trace every agent decision |
| Hosting | Railway (API + workers), Supabase (Postgres) | Migrate to Azure if Founders Hub credits land |
| Frontend | Next.js + shadcn/ui + Tailwind | Out of scope for v1 backend; mock with simple HTML |
| Eval harness | Promptfoo | Wired into CI |
| Test framework | pytest + pytest-asyncio + VCR.py | |

**Forbidden** without an explicit written ADR overriding this brief:
- ChromaDB, Pinecone, Qdrant, Weaviate, Cosmos DB, Azure AI Search
- LangChain (use LangGraph only)
- Direct Anthropic/OpenAI/Groq SDK calls (always via LiteLLM)
- GLM/Zhipu (reserved for tenant-opt-in non-PII tasks; not v1)
- Self-hosting LLMs in v1 (no GPU budget)
- Microservices (monolith)
- Kubernetes
- Stock prompt-chaining ("if-this-then-that-LLM") frameworks

## 4. Architecture in 30 seconds

A single LangGraph state machine runs per match request. Stages 1-5 and 7-9 are deterministic; stage 6 is the only agent.

```
validate_inputs → embed_query → retrieve (pgvector + BM25 + filters)
  → rerank (bge-reranker, local CPU) → enrich (parallel re-parse)
  → reason (Claude Sonnet, max 6 tool calls, max $1.50)
  → validate_output (8 deterministic checks; 1 loop-back allowed)
  → draft_invites (Groq) → persist (Postgres tx)

  → [out of graph] human approval → send_worker → Resend
```

Full spec in `AGENTIC_WORKFLOW.md`. Worked example in `USER_CASE_WALKTHROUGH.md`.

## 5. Target file structure

Create exactly this layout. Do not invent new top-level directories without a documented reason.

```
jobmatch/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py          # Auth, tenant context dependencies
│   │   ├── jobs.py          # POST /jobs, GET /jobs/{id}
│   │   ├── candidates.py    # POST /candidates, GET /candidates/{id}, DELETE
│   │   ├── matches.py       # POST /matches/run, GET /matches/{run_id}
│   │   └── invites.py       # GET /invites, POST /invites/{id}/approve, /reject
│   ├── core/
│   │   ├── config.py        # Pydantic Settings, reads from env
│   │   ├── security.py      # RLS context, encryption helpers
│   │   ├── litellm.yaml     # LLM router config
│   │   └── logging.py       # Logfire setup
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py       # async engine + session
│   │   ├── models.py        # SQLAlchemy 2.0 declarative
│   │   ├── repos.py         # Tenant-scoped repositories
│   │   └── migrations/      # Alembic
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── state.py         # PipelineState TypedDict + Pydantic models
│   │   ├── graph.py         # LangGraph StateGraph assembly
│   │   ├── nodes/
│   │   │   ├── validate.py
│   │   │   ├── embed.py
│   │   │   ├── retrieve.py
│   │   │   ├── rerank.py
│   │   │   ├── enrich.py
│   │   │   ├── reason.py    # The agent node
│   │   │   ├── verify.py    # validate_output + loop-back logic
│   │   │   ├── draft.py
│   │   │   └── persist.py
│   │   └── tools/           # Tools bound to the reasoner agent
│   │       ├── __init__.py
│   │       ├── candidate.py # get_candidate_profile
│   │       ├── parse.py     # parse_missing_fields
│   │       ├── rescore.py   # rescore_candidate
│   │       ├── more.py      # request_more_candidates
│   │       └── finalize.py  # finalize_shortlist
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── ingest_worker.py # parse + embed on resume upload
│   │   ├── match_worker.py  # runs the pipeline
│   │   └── send_worker.py   # post-approval send via Resend
│   ├── services/
│   │   ├── llm.py           # LiteLLM wrapper with cost tracking
│   │   ├── embeddings.py
│   │   ├── reranker.py      # BGE reranker singleton
│   │   ├── resume_parser.py # PDF/DOCX → text
│   │   ├── email.py         # Resend client
│   │   └── storage.py       # R2 upload/download
│   └── eval/
│       ├── gold/            # JSON gold-set fixtures
│       ├── runner.py        # Promptfoo runner
│       └── metrics.py       # Top-5 overlap, NDCG, citation faithfulness
├── tests/
│   ├── unit/
│   ├── contract/            # one test per pipeline node
│   ├── integration/         # full pipeline against test DB
│   ├── adversarial/         # prompt injection, cross-tenant, malformed input
│   └── conftest.py
├── alembic.ini
├── pyproject.toml
├── .env.example
├── docker-compose.yml       # Postgres + Redis for local dev
├── Makefile                 # make dev, make test, make eval
└── README.md
```

## 6. Build order (phases)

Build in this order. Each phase has a binary acceptance check. Do not advance to phase N+1 until phase N's acceptance check passes.

### Phase 0 — Bootstrap (target: ½ day)

- [ ] `pyproject.toml` with all deps from §3, pinned major versions
- [ ] `docker-compose.yml` runs Postgres-with-pgvector + Redis locally
- [ ] `app/main.py` returns `{"status":"ok"}` at `/health`
- [ ] Alembic initialized; one empty migration runs cleanly
- [ ] `make dev`, `make test`, `make eval` targets exist and don't crash
- **Acceptance:** `make dev` boots the API and a worker; `GET /health` returns 200.

### Phase 1 — Data layer + auth (target: 1-2 days)

- [ ] All 7 tables from `ARCHITECTURE_V2.md` §4 created via Alembic migrations
- [ ] Postgres RLS policies enforced on `candidates`, `jobs`, `matches`, `invites`, `audit_events`
- [ ] `app.tenant_id` setting set per-request via FastAPI dependency
- [ ] Clerk JWT validation, user→tenant mapping resolved
- [ ] `pgcrypto` enabled; `candidates.contact_jsonb_encrypted` round-trips
- **Acceptance:** an adversarial integration test attempting cross-tenant read FAILS at the DB layer (not the app layer).

### Phase 2 — Ingest path (target: 2 days)

- [ ] `POST /candidates` accepts a PDF/DOCX upload + consent metadata
- [ ] Resume uploaded to R2; raw bytes preserved
- [ ] `ingest_worker` parses (Groq llama-3.1-8b-instant), embeds (OpenAI), persists
- [ ] Dedup via `full_name_hash` + email
- [ ] `DELETE /candidates/{id}` cascades: DB row, R2 blob, related matches anonymized
- **Acceptance:** upload 100 real resumes, ≥90% parse with all required fields populated.

### Phase 3 — Retrieval + rerank (deterministic core) (target: 2 days)

- [ ] `POST /jobs` accepts a JD; embeds it
- [ ] Hybrid SQL query (pgvector + BM25 + structured filters) returns top 50 in <300ms
- [ ] BGE reranker loaded as a worker singleton; reranks 50 → 15 in <2s
- [ ] All queries tenant-scoped; verified by adversarial test
- **Acceptance:** given 1k candidates and a JD, retrieval+rerank returns top 15 in <3s p95 with correct tenant isolation.

### Phase 4 — Pipeline graph + agent (target: 4-5 days, this is the hard one)

- [ ] `app/pipeline/state.py` defines `PipelineState` TypedDict + all Pydantic schemas
- [ ] `app/pipeline/graph.py` assembles the LangGraph StateGraph per `AGENTIC_WORKFLOW.md` §1
- [ ] Each node in `app/pipeline/nodes/` is a pure async function: `state -> state`
- [ ] Reasoner node uses LiteLLM, Anthropic Claude Sonnet, with tool bindings
- [ ] Hard caps enforced: max 6 tool calls, max $1.50 cost, max 45s wall-clock
- [ ] Rubric is derived BEFORE the agent runs (separate Groq call), passed in as fixed context
- [ ] All 5 agent tools implemented; all are read-only or scope-limited
- [ ] `validate_output` runs 8 deterministic checks per `AGENTIC_WORKFLOW.md` §2.7
- [ ] **Citation faithfulness check** (exact substring match against raw resume) MUST be implemented
- [ ] Loop-back capped at 1 per run
- [ ] Full pipeline trace serialized into `matches.agent_trace_jsonb`
- **Acceptance:** end-to-end run on seed data completes in <30s with all 8 validation checks passing; trace is queryable; cost is under $0.30.

### Phase 5 — Drafts + human approval gate (target: 1-2 days)

- [ ] `draft_invites` produces 5 emails via Groq with strict JSON schema output
- [ ] Drafts stored in `invites` table with `status=awaiting_approval`
- [ ] `POST /invites/{id}/approve` requires authenticated recruiter; sets `approved_by`, `approved_at`
- [ ] `POST /invites/{id}/reject` with optional reason
- [ ] `send_worker` consumes only invites where `status=approved`; uses Resend; updates status to `sent`
- [ ] **`send_email` MUST NOT exist as an agent-callable tool.** Verified by inspecting agent's tool registry.
- **Acceptance:** an adversarial test that sets `status=approved` directly via API call (without going through approval endpoint) fails authorization; only the dedicated approval endpoint can set that state.

### Phase 6 — Eval harness (target: 2 days; do not skip)

- [ ] `eval/gold/` contains ≥10 hand-crafted (JD, candidate_pool, expected_top_5) tuples to start
- [ ] `eval/runner.py` runs full pipeline against gold set, writes scores to `eval/results/`
- [ ] Metrics: top-5 overlap, NDCG@5, citation faithfulness (must be 100%), hard-requirement correctness
- [ ] CI job runs eval on every PR; blocks merge on regression >5%
- **Acceptance:** `make eval` runs in <5 min and produces a metrics JSON; CI is wired.

### Phase 7 — Observability + cost telemetry (target: 1 day)

- [ ] LiteLLM cost callback writes to `audit_events` per call
- [ ] Logfire spans for every node, tool call, LLM call
- [ ] Sentry captures exceptions with run_id + tenant_id tags
- [ ] Per-run cost visible via `GET /matches/{run_id}`
- **Acceptance:** for a sample run, the trace UI shows every node, every LLM call with cost, every tool call.

### Phase 8 — Polish + first design partner (target: 1-2 weeks, ongoing)

- [ ] Minimal HTML dashboard (Jinja templates is fine; React not required for v1) for: jobs list, run a match, review shortlist, approve/reject invites
- [ ] Onboarding flow: tenant signup, first candidate upload
- [ ] One real recruiter using it weekly; weekly feedback loop
- **Acceptance:** one human runs ≥5 real matches per week and is willing to give a quote.

**MVP DONE** when all 12 items in `AGENTIC_WORKFLOW.md` §10 pass.

## 7. Code conventions (non-negotiable)

- **Async/await everywhere.** No sync-over-async, no blocking calls inside coroutines (CPU-heavy stuff like the reranker goes through `asyncio.to_thread`).
- **Pydantic v2 for every IO boundary.** API request/response, LLM tool inputs/outputs, config, message schemas. No raw dicts crossing layer boundaries.
- **No `print`.** Use the configured logger / Logfire.
- **No `Any` in type hints** unless wrapping a third-party untyped surface; even then, narrow at the boundary.
- **No bare `except`.** Catch specifically. Re-raise unless you can fully handle.
- **Tenant ID is required everywhere.** Every repo method takes `tenant_id` as the first arg, OR uses the request-scoped context. Functions that touch DB without tenant scope require a `# noqa: tenant-required` comment with justification.
- **Tools the agent calls return errors as values, not exceptions.** `ToolResult(success=bool, data=..., error=...)`. Exceptions in tool code are bugs.
- **Prompts live in `app/pipeline/nodes/prompts/*.txt` files.** Versioned. Loaded with `importlib.resources`. Never inline a multi-line prompt in code.
- **Every LLM call goes through `app/services/llm.py`.** That wrapper enforces: cost tracking, retry policy, model fallback, prompt-hash logging, PII redaction in logs.
- **Repository pattern.** No `db.execute(...)` outside `app/db/repos.py`. API/pipeline code never sees raw SQL.
- **Idempotency.** Every Celery task takes a `run_id` or other natural key; reruns must not double-write.
- **Migrations are forward-only.** Generate via `alembic revision --autogenerate`, review, commit. No editing past migrations.
- **Time is UTC.** `datetime.now(UTC)` everywhere. Never `datetime.now()`.
- **Small functions.** If a function is >40 lines, split it. If a file is >300 lines, split it.

## 8. Safety invariants (REGRESSION = STOP THE WORLD)

These conditions must hold at every commit. Violations are P0 bugs.

1. **No tool callable by the agent has side effects on candidates, invites, or external systems.** Specifically: no `send_email`, no `update_candidate`, no `delete_*`, no `http_request` in the agent's palette. Side effects happen in pipeline nodes (which the agent doesn't control) or in workers triggered by human approval.
2. **Citation faithfulness check exists and runs on every output.** Any output where a quoted citation is not an exact substring of the underlying resume MUST trigger a loop-back. Removing or weakening this check is forbidden.
3. **Tenant isolation is enforced at the DB layer (RLS), not just the app layer.** App-layer tenant filtering is defense-in-depth, not the primary boundary.
4. **`send_worker` refuses to send any invite whose `status != "approved"` or `approved_by IS NULL`.**
5. **No raw resume text or candidate PII enters LLM logs.** LiteLLM wrapper redacts known PII fields and replaces them with hashes before logging.
6. **Cost cap per run ($1.50) is enforced live.** A run that exceeds it is killed mid-flight, not after.
7. **GDPR delete cascades to R2 within 30 days.** A scheduled job verifies orphaned blobs and removes them.
8. **No PII in error messages returned to API clients.** Internal logs may contain it (encrypted); API responses must not.

If a code change weakens any of the above, it must include an explicit ADR in `/docs/adr/` and the user (Rami) must sign off.

## 9. Testing requirements

| Test type | Required for | Tooling |
|---|---|---|
| Unit | Every Pydantic model, every pure function | pytest |
| Contract | Every pipeline node (state-in → state-out) | pytest with state factories |
| Integration | Full pipeline against real Postgres + mocked LLM cassettes | pytest + VCR.py |
| Adversarial | Cross-tenant access, prompt injection, malformed input, missing fields, non-UTF8, huge files | pytest |
| Eval | Gold-set match quality | Promptfoo |
| Load | 50 concurrent runs once, before launch | Locust |

Coverage target: **70% line coverage** across `app/`, **100% on safety-critical paths** (`app/pipeline/nodes/verify.py`, `app/services/llm.py`, RLS setup, `send_worker`).

A PR that adds a new pipeline node without a contract test is rejected.
A PR that touches a prompt without an eval-suite delta is rejected.

## 10. Things you will be tempted to do — DO NOT

- **Don't add ChromaDB or any other vector store.** pgvector is sufficient until 10M vectors. We will not hit that in year 1.
- **Don't make the agent loop free-form.** It runs at most 6 tool calls inside one bounded node. The pipeline shape is fixed.
- **Don't add `send_email` as an agent tool.** Ever. If you think you need it, you don't — the human approval gate is the product.
- **Don't add LangChain.** LangGraph only.
- **Don't write prompts in code.** Always in `prompts/*.txt` files, loaded by name.
- **Don't add multi-agent debate, critique chains, or planner agents.** One agent, one node, one bounded loop.
- **Don't add a custom-trained model.** Year 1 is off-the-shelf only.
- **Don't add a separate worker for every node.** One Celery worker runs the whole pipeline; concurrency is per-run, not per-node.
- **Don't introduce GraphQL, gRPC, or a websocket layer.** REST + JSON + polling is fine for v1.
- **Don't write a cron-based scheduler in app code.** Use Celery beat or a simple Postgres-backed job table.
- **Don't add Stripe, billing, or quotas yet.** Stub it. Phase 8+.
- **Don't add an admin UI for tenant management yet.** SQL is fine for v1.
- **Don't try to make the LLM produce embeddings.** Embeddings come from `text-embedding-3-small`.

## 11. Environment variables (`.env.example`)

```
# Database
DATABASE_URL=postgresql+asyncpg://jobmatch:dev@localhost:5432/jobmatch
REDIS_URL=redis://localhost:6379/0

# LLM providers (LiteLLM picks these up)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GROQ_API_KEY=

# Storage
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_RESUMES=

# Email
RESEND_API_KEY=
RESEND_FROM_EMAIL=

# Auth
CLERK_SECRET_KEY=
CLERK_PUBLISHABLE_KEY=
CLERK_JWT_PUBLIC_KEY=

# Observability
SENTRY_DSN=
LOGFIRE_TOKEN=

# App
APP_ENV=dev               # dev | staging | prod
APP_LOG_LEVEL=INFO
PIPELINE_COST_CAP_USD=1.50
PIPELINE_TIME_CAP_S=45
TENANT_MONTHLY_COST_CAP_USD=50

# Encryption
DB_ENCRYPTION_KEY=         # 32-byte base64
```

## 12. How to communicate progress

When you complete a phase, write a short ADR-style note to `docs/adr/NNNN-phase-N-complete.md` with:
- What you built
- Trade-offs you made
- What you skipped or stubbed and why
- What's next

When you encounter a real architectural question not answered by these docs, STOP and write a question to `docs/questions/OPEN.md` instead of guessing. Bias toward stopping over guessing.

## 13. Quick reference — first commands to run

```bash
# Bootstrap
poetry install                          # or: uv sync
docker compose up -d postgres redis
make migrate                            # alembic upgrade head
make seed                               # load 100 fake resumes + 5 JDs

# Develop
make dev                                # uvicorn + celery worker
make test                               # pytest
make eval                               # run gold-set evals
make lint                               # ruff + mypy

# One-off: run a pipeline manually
python -m app.scripts.run_match \
  --tenant-id seed_tenant_01 \
  --job-id seed_job_01
```

## 14. The one north-star metric

Every architectural decision should improve at least one of:

1. **Top-5 match quality on the gold set** (NDCG@5)
2. **Latency p95 of a full match run** (target ≤30s)
3. **Cost per match run** (target ≤$0.30)
4. **Auditability** (can a regulator answer "why was candidate X ranked Y" from the trace alone?)

If a change degrades one of these without improving another, reject it.

---

**End of brief. Build phase 0 first. Acknowledge in `docs/adr/0001-build-started.md` before writing any code.**
