# Phase 2 — Ingest Path Design

**Date:** 2026-04-24  
**Status:** Approved  
**Phase:** 2 — Ingest path (CLAUDE.md §6)

---

## Summary

Phase 2 builds the resume upload and processing pipeline:
- `POST /candidates` accepts a file + explicit name/email + consent metadata, deduplicates synchronously, and returns a `candidate_id` immediately
- A Celery worker (`ingest.process_resume`) does LLM-based structured parsing (Groq) and embedding (OpenAI) asynchronously
- `DELETE /candidates/{id}` soft-deletes the row, anonymizes related matches, and async-purges the R2 blob

---

## Key Decisions

| Decision | Choice | Reason |
|---|---|---|
| Dedup collision | Return existing candidate, `200 OK` | Idempotent — safe for bulk imports with duplicates |
| Name + email source | Explicit form fields | Enables synchronous dedup before LLM parsing; consistent with ATS systems |
| Consent fields | `consent_given_at`, `consent_version`, `consent_ip` | Full GDPR audit trail |
| `consent_ip` storage | Inside Fernet-encrypted contact blob | PII — never in plaintext columns |
| `consent_version` storage | New `TEXT NOT NULL` column on `candidates` | Not PII; useful for compliance queries |
| Max file size | 5 MB | Covers real-world resumes; rejects accidental large uploads |
| Parsed structured fields | 8 fields (see below) | Sufficient for Phase 3 retrieval filters |
| Worker idempotency guard | Check `resume_text != ""` on entry | Safe Celery retries without double-processing |
| R2 delete | Async Celery task | Decouples delete latency from API response; satisfies 30-day GDPR window |

---

## 1. API Endpoints

### `POST /candidates`

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | `str` | Yes | Candidate full name |
| `email` | `str` | Yes | Candidate email |
| `consent_given_at` | `datetime` (ISO 8601) | Yes | When candidate gave GDPR consent |
| `consent_version` | `str` | Yes | Privacy policy version, e.g. `"v1.2"` |
| `consent_ip` | `str` | Yes | IP address at consent time |
| `file` | `UploadFile` | Yes | PDF or DOCX/DOC, max 5 MB |

**Flow:**

```
1. Validate file extension (pdf / docx / doc) and size (≤5 MB) → 422 on failure
2. Normalize name (strip + lowercase) and email (lowercase)
3. Compute SHA-256 hashes → full_name_hash, email_hash
4. CandidateRepo.find_by_hashes(tenant_id, full_name_hash, email_hash)
   → if hit: return existing candidate with 200 OK
5. Encrypt {name, email, consent_ip} via Fernet → contact_jsonb_encrypted
6. Generate candidate_id (UUID4)
7. Upload file bytes to R2: key = {tenant_id}/{candidate_id}/{filename}
8. CandidateRepo.create(... resume_text="", structured_jsonb={}, embedding=NULL, consent_version=...)
9. Dispatch Celery task: ingest.process_resume(run_id, candidate_id, tenant_id)
10. AuditRepo.log(event_type="candidate_uploaded", entity_id=candidate_id)
11. Return 202 Accepted: {id, status: "processing"}
```

**Responses:**
- `202 Accepted` — new candidate created, processing queued
- `200 OK` — duplicate detected, returns existing candidate
- `413 Request Entity Too Large` — file exceeds 5 MB
- `422 Unprocessable Entity` — invalid file type or missing/invalid form fields
- `401 Unauthorized` — missing or invalid Clerk JWT

---

### `GET /candidates/{id}`

Returns current candidate status. `status` is:
- `"processing"` if `embedding IS NULL`
- `"ready"` if `embedding IS NOT NULL`

Returns `404` if not found or soft-deleted.

---

### `DELETE /candidates/{id}`

**Flow:**

```
1. CandidateRepo.soft_delete(tenant_id, candidate_id) → sets deleted_at
   → 404 if not found or already deleted
2. MatchRepo.anonymize_candidate(tenant_id, candidate_id)
   → replaces name/justification with "[removed]" in shortlist_jsonb across all matches
3. Dispatch Celery task: ingest.delete_r2_object(r2_key)
4. AuditRepo.log(event_type="candidate_deleted", entity_id=candidate_id)
5. Return 204 No Content
```

---

## 2. Ingest Worker

### `ingest.process_resume(run_id, candidate_id, tenant_id)`

**Idempotency:** exits cleanly if `candidate.resume_text != ""` (already processed).

**Flow:**

```
1. Fetch candidate row (explicit tenant_id filter — no RLS session variable in worker)
2. Download file bytes from R2: storage.download_resume(r2_key)
3. resume_parser.extract_text(data, filename) → plain text
4. Call Groq llama-3.1-8b-instant via llm.chat_completion with parse_resume.txt prompt
5. Validate JSON output with ParsedResume Pydantic model
   → on failure: retry once with stricter prompt
   → on second failure: persist structured_jsonb={"parse_error": true}, continue
6. Re-encrypt contact blob with phone + address added (if found by LLM)
7. embed_text(resume_text) via OpenAI text-embedding-3-small
8. CandidateRepo.update_after_parse(candidate_id, resume_text, structured_jsonb, embedding, contact_jsonb_encrypted)
9. AuditRepo.log(event_type="candidate_parsed", cost_usd=llm_cost)
```

### `ingest.delete_r2_object(r2_key)`

Calls `storage.delete_resume(key)`. Idempotent (R2 delete is a no-op for missing keys). Logs result.

---

## 3. ParsedResume Schema

```python
class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: int | None

class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    languages: list[str]
    location: str | None
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    job_titles: list[str]
    certifications: list[str]
    phone: str | None      # added to encrypted contact blob
    address: str | None    # added to encrypted contact blob
```

Prompt: `app/services/prompts/parse_resume.txt` — loaded via `importlib.resources`.

---

## 4. Schema Changes

**Migration `0010_add_consent_version_to_candidates.py`:**
```sql
ALTER TABLE candidates
  ADD COLUMN consent_version TEXT NOT NULL DEFAULT 'v1';
```

**`Candidate` model:** add `consent_version: Mapped[str]` column.

**`CandidateRepo` new methods:**
- `update_after_parse(candidate_id, *, resume_text, structured_jsonb, embedding, contact_jsonb_encrypted)` — updates the row post-parse
- `get_status(tenant_id, candidate_id)` → `{id, status}` where status is `"processing"` if `embedding IS NULL` else `"ready"`

**`MatchRepo` new method:**
- `anonymize_candidate(tenant_id, candidate_id)` — replaces matching candidate entries in `shortlist_jsonb` with `"[removed]"` across all matches for the tenant

---

## 5. New Files

| Path | Purpose |
|---|---|
| `app/api/candidates.py` | POST, GET, DELETE endpoints |
| `app/services/prompts/parse_resume.txt` | Groq parse prompt |
| `app/db/migrations/versions/0010_add_consent_version.py` | Add consent_version column |
| `tests/unit/test_candidate_hashing.py` | Hash normalization tests |
| `tests/unit/test_parsed_resume_schema.py` | Pydantic schema validation tests |
| `tests/contract/test_ingest_worker.py` | Worker contract test with VCR cassettes |
| `tests/integration/test_candidates_api.py` | Full API integration tests |
| `tests/adversarial/test_candidate_upload.py` | Cross-tenant, bad files, size limits |

**Modified files:**
- `app/db/models.py` — add `consent_version` column to `Candidate`
- `app/db/repos.py` — add `update_after_parse`, `get_status` to `CandidateRepo`; add `anonymize_candidate` to `MatchRepo`
- `app/workers/ingest_worker.py` — implement `process_resume` and `delete_r2_object`

---

## 6. Testing

| Type | File | What it covers |
|---|---|---|
| Unit | `test_candidate_hashing.py` | Normalization; `"Jane Doe"` and `" jane doe "` produce identical hashes |
| Unit | `test_parsed_resume_schema.py` | Good JSON validates; missing fields rejected; seniority_level coercion |
| Contract | `test_ingest_worker.py` | Worker updates resume_text, structured_jsonb, embedding; idempotency |
| Integration | `test_candidates_api.py` | 202 on first upload; 200 on duplicate; 204 on delete; 404 post-delete |
| Adversarial | `test_candidate_upload.py` | Cross-tenant 404; >5 MB → 413; .exe → 422; binary PDF → parse_error; bad timestamp → 422 |

Coverage target: **100%** on `ingest_worker.py` and dedup path in `candidates.py`.

---

## 7. Safety Invariants

- `consent_ip` never appears in a plaintext DB column or in logs
- `resume_text` and `contact_jsonb_encrypted` never logged (LiteLLM wrapper redacts known PII keys)
- R2 deletion is always async — never blocks the API response
- Worker never calls any email or external-side-effect service
- All DB queries in the worker include explicit `tenant_id` filter (no RLS session variable)
