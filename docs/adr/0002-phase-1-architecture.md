# ADR 0002 — Phase 1: Data Layer + Auth Architecture

**Date:** 2026-04-23  
**Status:** Accepted

---

## What we're building

Seven tables, Postgres RLS enforced via `app.tenant_id` session variable, Clerk JWT validation
replacing the stub `X-Tenant-Id` header, and pgcrypto encryption on candidate PII.

---

## 1. Table schemas

### `tenants`
Primary identity for each company using JobMatch.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `name` | `TEXT NOT NULL` | Company name |
| `clerk_org_id` | `TEXT UNIQUE NOT NULL` | Maps Clerk org → tenant |
| `monthly_cost_cap_usd` | `NUMERIC(10,4) DEFAULT 50` | Per CLAUDE.md |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

No RLS needed — only service role reads this table to resolve JWT → tenant.

### `users`
Individual recruiter accounts within a tenant.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `clerk_user_id` | `TEXT UNIQUE NOT NULL` | Clerk subject claim |
| `email` | `TEXT NOT NULL` | |
| `role` | `TEXT NOT NULL DEFAULT 'recruiter'` | recruiter \| admin |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid`

### `candidates`
One row per parsed resume. PII encrypted at rest.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `full_name_hash` | `TEXT NOT NULL` | SHA-256 of normalized full name for dedup |
| `email_hash` | `TEXT NOT NULL` | SHA-256 of email for dedup |
| `contact_jsonb_encrypted` | `BYTEA NOT NULL` | pgcrypto-encrypted JSON: name, email, phone, address |
| `resume_text` | `TEXT NOT NULL` | Parsed plain text (not PII fields; still tenant-isolated) |
| `structured_jsonb` | `JSONB NOT NULL DEFAULT '{}'` | Parsed fields: skills, experience_years, education, languages |
| `embedding` | `vector(1536)` | text-embedding-3-small output |
| `r2_key` | `TEXT NOT NULL` | R2 object key for raw file |
| `consent_given_at` | `TIMESTAMPTZ NOT NULL` | GDPR consent timestamp |
| `deleted_at` | `TIMESTAMPTZ` | Soft delete; hard purge within 30 days |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid AND deleted_at IS NULL`

Unique constraint: `(tenant_id, full_name_hash, email_hash)` for dedup.

### `jobs`
Job descriptions submitted by recruiters.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `created_by` | `UUID NOT NULL FK → users.id` | |
| `title` | `TEXT NOT NULL` | |
| `description_text` | `TEXT NOT NULL` | Raw JD text |
| `structured_jsonb` | `JSONB NOT NULL DEFAULT '{}'` | Parsed requirements: skills, seniority, location_required, etc. |
| `embedding` | `vector(1536)` | JD embedding |
| `status` | `TEXT NOT NULL DEFAULT 'active'` | active \| archived |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid`

### `matches`
One row per pipeline run (one job → N candidates).

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | Also the Celery `run_id` for idempotency |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `job_id` | `UUID NOT NULL FK → jobs.id` | |
| `triggered_by` | `UUID NOT NULL FK → users.id` | |
| `status` | `TEXT NOT NULL DEFAULT 'pending'` | pending \| running \| completed \| failed |
| `shortlist_jsonb` | `JSONB` | Ordered list of {candidate_id, score, justification, citations} |
| `agent_trace_jsonb` | `JSONB` | Full LangGraph trace: every node, tool call, LLM call, cost |
| `cost_usd` | `NUMERIC(10,6)` | Total LLM cost for this run |
| `duration_s` | `NUMERIC(8,3)` | Wall-clock seconds |
| `error` | `TEXT` | Set on failure |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `completed_at` | `TIMESTAMPTZ` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid`

### `invites`
Draft interview-invite emails awaiting human approval.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `match_id` | `UUID NOT NULL FK → matches.id` | |
| `candidate_id` | `UUID NOT NULL FK → candidates.id` | |
| `job_id` | `UUID NOT NULL FK → jobs.id` | |
| `draft_subject` | `TEXT NOT NULL` | |
| `draft_body` | `TEXT NOT NULL` | |
| `status` | `TEXT NOT NULL DEFAULT 'awaiting_approval'` | awaiting_approval \| approved \| rejected \| sent \| failed |
| `approved_by` | `UUID FK → users.id` | NULL until approved |
| `approved_at` | `TIMESTAMPTZ` | NULL until approved |
| `rejected_reason` | `TEXT` | |
| `resend_message_id` | `TEXT` | Populated after send |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid`

Safety invariant: `send_worker` checks `status = 'approved' AND approved_by IS NOT NULL` before calling Resend.

### `audit_events`
Immutable append-only log for EU AI Act auditability.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK` | |
| `tenant_id` | `UUID NOT NULL FK → tenants.id` | |
| `actor_id` | `UUID FK → users.id` | NULL for system events |
| `event_type` | `TEXT NOT NULL` | llm_call \| tool_call \| invite_approved \| invite_sent \| candidate_deleted, etc. |
| `entity_type` | `TEXT` | candidate \| job \| match \| invite |
| `entity_id` | `UUID` | FK target (not enforced — entity may be soft-deleted) |
| `payload_jsonb` | `JSONB NOT NULL DEFAULT '{}'` | Event data; PII redacted |
| `cost_usd` | `NUMERIC(10,6)` | For LLM events |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

RLS: `tenant_id = current_setting('app.tenant_id')::uuid`

No UPDATE/DELETE allowed on this table (enforced via RLS + GRANT).

---

## 2. RLS design

Every protected table uses the same pattern:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

The `true` flag makes `current_setting` return NULL instead of raising an error when the
variable is not set — so a forgot-to-set bug returns zero rows rather than a 500.

The FastAPI dependency sets the variable at the start of every request via:

```sql
SET LOCAL app.tenant_id = '<uuid>';
```

`SET LOCAL` scopes the variable to the current transaction, so it's automatically cleared
when the transaction commits or rolls back. No cleanup needed.

Postgres role: the API connects as `jobmatch_app` (not superuser). This role has SELECT,
INSERT, UPDATE, DELETE on all tables but no BYPASSRLS. The migration role (`jobmatch_migrate`)
has BYPASSRLS to run Alembic.

---

## 3. Clerk JWT → tenant flow

```
Request arrives
  → FastAPI dependency `get_current_user()`
  → Extract Bearer token from Authorization header
  → Fetch Clerk JWKS (cached, refreshed every 1h)
  → Verify JWT signature + exp + iss
  → Extract claims: sub (clerk_user_id), org_id (clerk_org_id)
  → SELECT tenant_id FROM tenants WHERE clerk_org_id = :org_id  [bypass RLS]
  → SELECT id FROM users WHERE clerk_user_id = :sub AND tenant_id = :tenant_id
  → SET LOCAL app.tenant_id = :tenant_id
  → Yield (tenant_id, user) to route handler
```

JWKS URL: `https://clerk.yourdomain.com/.well-known/jwks.json` (from `CLERK_PUBLISHABLE_KEY`).
Cached in an in-process `dict` with TTL; refreshed on 401 from signature verification.

Library: `python-jose[cryptography]` for JWT decode (already in pyproject.toml).

---

## 4. pgcrypto encryption

`candidates.contact_jsonb_encrypted` stores:

```json
{"name": "Jane Doe", "email": "jane@example.com", "phone": "+33612345678", "address": "Paris, FR"}
```

Encrypted with Fernet (symmetric AES-128-CBC + HMAC-SHA256) using `DB_ENCRYPTION_KEY`.
The `app/core/security.py` helpers (`encrypt_bytes` / `decrypt_bytes`) handle this.

Why Fernet over pgcrypto's `pgp_sym_encrypt`? App-layer encryption means the key never
reaches the DB process — better for GDPR key deletion (delete the key, data is inaccessible).
pgcrypto is still enabled for the `gen_random_uuid()` function and for hashing.

---

## 5. Alembic migration sequence

`0002_create_extensions.py` — ensure vector, pg_trgm, pgcrypto are created  
`0003_create_tenants_users.py` — tenants + users tables, no RLS (service-role tables)  
`0004_create_candidates.py` — candidates + RLS policy + trgm index + ivfflat index  
`0005_create_jobs.py` — jobs + RLS policy + ivfflat index  
`0006_create_matches.py` — matches + RLS policy  
`0007_create_invites.py` — invites + RLS policy  
`0008_create_audit_events.py` — audit_events + RLS policy + no-update trigger  
`0009_create_roles.py` — CREATE ROLE jobmatch_app, GRANT statements  

Each migration is a standalone file; forward-only per CLAUDE.md conventions.

---

## 6. Adversarial test plan

`tests/adversarial/test_tenant_isolation.py`

1. Create tenant A + tenant B (each with a user, bypassing RLS via migration role).
2. Insert a candidate under tenant A.
3. Open a DB session, SET LOCAL app.tenant_id = tenant_B_id.
4. Assert `SELECT * FROM candidates` returns zero rows.
5. Assert the same with jobs, matches, invites, audit_events.
6. Assert that attempting to read tenant A's match via the API with a tenant B JWT returns 404.

This test must use a real Postgres instance (no mocking). It's an integration test, not a unit test.

---

## Trade-offs

- **Fernet vs pgcrypto for PII**: Chose app-layer Fernet. Slower (round-trip through Python) but
  the key never touches Postgres — cleaner GDPR key-deletion story.
- **`SET LOCAL` vs `SET` for tenant context**: `SET LOCAL` is transaction-scoped (auto-reset on
  commit/rollback). `SET` is session-scoped and leaks across requests in connection pools. Always
  use `SET LOCAL`.
- **JWKS caching**: Avoid a DB/network round-trip per request. 1h TTL is safe given Clerk's
  key rotation schedule. On 401, refresh immediately and retry once.
- **`audit_events` immutability**: Enforced at RLS level (no UPDATE/DELETE policy), not at the
  table level (no trigger needed in v1). Sufficient for now.

---

## What's next

Phase 2 — Ingest path: `POST /candidates` accepting PDF/DOCX, R2 upload, `ingest_worker`
parsing + embedding, dedup, `DELETE /candidates/{id}` GDPR cascade.
