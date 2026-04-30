# JobMatch — Codebase Tutorial

This document walks through every layer of the codebase in the order a request actually travels through it. By the end you will understand why each file exists, what every design decision is protecting, and how the ingest path works end-to-end.

---

## 1. What the system does (30 seconds)

An HR recruiter uploads a job description. The system searches a company's pool of candidate resumes, ranks the top 5 using a mix of vector similarity and an AI agent, drafts personalised interview emails, and holds them for human approval before sending.

This tutorial focuses on **Phase 2: the ingest path** — everything that happens from the moment a recruiter uploads a resume PDF to the moment that candidate is searchable.

---

## 2. The file structure and what each piece does

```
app/
├── main.py              ← FastAPI app factory + router wiring
├── core/
│   ├── config.py        ← All env vars in one Pydantic Settings class
│   ├── security.py      ← Fernet encryption + SHA-256 hashing helpers
│   └── logging.py       ← Logfire setup
├── api/
│   ├── deps.py          ← Clerk JWT validation → AuthContext
│   └── candidates.py    ← POST /candidates, GET, DELETE
├── db/
│   ├── models.py        ← SQLAlchemy 2.0 table definitions
│   ├── repos.py         ← Every DB query lives here, nowhere else
│   ├── session.py       ← Async engine + session factory
│   └── migrations/      ← Alembic migration scripts
├── services/
│   ├── llm.py           ← All LLM calls go through here
│   ├── embeddings.py    ← text-embedding-3-small wrapper
│   ├── resume_parser.py ← PDF/DOCX → plain text
│   ├── storage.py       ← Cloudflare R2 upload/download/delete
│   └── prompts/
│       └── parse_resume.txt  ← Groq parsing prompt
└── workers/
    ├── celery_app.py    ← Celery broker config
    └── ingest_worker.py ← Background parse + embed tasks
```

---

## 3. The configuration layer (`app/core/config.py`)

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", ...)

    database_url: str = "postgresql+asyncpg://jobmatch:dev@localhost:5432/jobmatch"
    openai_api_key: str = ""
    groq_api_key: str = ""
    db_encryption_key: str = ""
    pipeline_cost_cap_usd: float = 1.50
    # ... all other env vars
```

`BaseSettings` from `pydantic-settings` reads each field from the matching env var automatically (e.g. `OPENAI_API_KEY` populates `openai_api_key`). The single `settings` singleton is imported everywhere:

```python
from app.core.config import settings

engine = create_async_engine(settings.database_url, ...)
```

**Why a singleton?** It's loaded once at import time. Tests can't easily swap it — but they don't need to, because tests bypass auth and mock external calls rather than re-configuring settings.

---

## 4. The database layer

### 4a. Models (`app/db/models.py`)

SQLAlchemy 2.0 uses a typed `Mapped[T]` syntax. Every column declaration is fully typed:

```python
class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))

    # PII never stored in plaintext — always Fernet-encrypted bytes
    contact_jsonb_encrypted: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    # Raw resume text for BM25 search (Phase 3)
    resume_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured fields parsed by Groq
    structured_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # 1536-dimensional vector for cosine similarity search
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    # R2 key so we can download the original file later
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Soft-delete: NULL means active, timestamp means deleted
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
```

Two things worth noting:

**`embedding` starts as NULL.** When a candidate is first uploaded, only the raw file is saved. The Celery worker later fills in `resume_text`, `structured_jsonb`, and `embedding`. The `status` field in API responses is derived from this:

```python
"status": "processing" if row.embedding is None else "ready"
```

**`TIMESTAMPTZ` is not a direct SQLAlchemy import.** It's a shorthand defined at the top of `models.py`:

```python
from sqlalchemy import TIMESTAMP
TIMESTAMPTZ = TIMESTAMP(timezone=True)
```

`sqlalchemy.dialects.postgresql` has no `TIMESTAMPTZ` — this is a common confusion. `TIMESTAMP(timezone=True)` is the correct cross-dialect equivalent.

### 4b. Dedup constraints

```python
__table_args__ = (
    UniqueConstraint("tenant_id", "full_name_hash", "email_hash", name="candidates_dedup_uq"),
    Index("candidates_embedding_idx", "embedding",
          postgresql_using="ivfflat", postgresql_ops={"embedding": "vector_cosine_ops"}),
)
```

The unique constraint on `(tenant_id, full_name_hash, email_hash)` enforces that the same person can't be uploaded twice per tenant. The IVFFlat index on `embedding` is what makes vector similarity search fast (Phase 3).

### 4c. The repository pattern (`app/db/repos.py`)

**No `db.execute(...)` calls outside this file.** API code and pipeline code never touch raw SQL. Every query lives in a repo class:

```python
class CandidateRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db                              # injected, never created here

    async def find_by_hashes(
        self, tenant_id: uuid.UUID, full_name_hash: str, email_hash: str
    ) -> Candidate | None:
        result = await self._db.execute(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id,  # always scoped to tenant
                Candidate.full_name_hash == full_name_hash,
                Candidate.email_hash == email_hash,
                Candidate.deleted_at.is_(None),    # soft-delete filter
            )
        )
        return result.scalar_one_or_none()
```

Every public method takes `tenant_id` as its first argument. This is a belt-and-suspenders rule: even if Postgres RLS (row-level security) is filtering rows, the application layer also scopes every query explicitly.

### 4d. The session (`app/db/session.py`)

```python
engine = create_async_engine(settings.database_url, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,     # avoids lazy-load errors after commit
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        async with session.begin():   # auto-commit or rollback
            yield session
```

`get_db` is a FastAPI dependency. `session.begin()` wraps the whole request in a transaction — if anything raises, the entire transaction rolls back. `expire_on_commit=False` means ORM objects remain readable after the transaction commits (otherwise SQLAlchemy would expire all attributes and trigger lazy loads on already-closed connections).

---

## 5. The security layer (`app/core/security.py`)

### Encryption

```python
from cryptography.fernet import Fernet

def _get_fernet() -> Fernet:
    key = settings.db_encryption_key
    if not key:
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()   # dev throwaway
    raw = base64.urlsafe_b64decode(key.encode() + b"==")
    return Fernet(base64.urlsafe_b64encode(raw[:32]))

def encrypt_bytes(data: bytes) -> bytes:
    return _get_fernet().encrypt(data)

def decrypt_bytes(data: bytes) -> bytes:
    return _get_fernet().decrypt(data)
```

Fernet is symmetric AES-128-CBC with HMAC-SHA256. Every call to `_get_fernet()` creates a new `Fernet` instance from the same key — this is fine because Fernet is stateless.

**The GDPR reason:** candidate names, emails, and addresses must be encrypted at rest so that if the database were breached, PII would be unreadable. The contact blob is stored as a JSON object encrypted into `BYTEA`:

```python
contact_blob = json.dumps({
    "name": name.strip(),
    "email": email.strip().lower(),
    "consent_ip": consent_ip,
    "phone": None,    # filled in later by the ingest worker
    "address": None,
}).encode()

contact_jsonb_encrypted = encrypt_bytes(contact_blob)
```

### Hashing for dedup

```python
def hash_field(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()
```

Names and emails are hashed one-way for deduplication. The hash is checked before inserting a new row. If `full_name_hash + email_hash` already exists for this tenant, the upload is a no-op (returns the existing candidate). You can deduplicate without ever storing or comparing plaintext PII.

---

## 6. The auth layer (`app/api/deps.py`)

Every API request is authenticated by a FastAPI dependency:

```python
async def get_current_user(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
```

The flow:

1. Extract `Bearer <token>` from the `Authorization` header
2. Fetch Clerk's JWKS (JSON Web Key Set) — cached for 1 hour
3. Decode and verify the JWT signature using the RSA public key
4. Extract `sub` (Clerk user ID) and `org_id` (Clerk organisation ID) from the claims
5. Look up internal `tenant_id` and `user_id` from the database
6. **Set a Postgres session variable:** `SET LOCAL app.tenant_id = '<uuid>'`

That last step activates Postgres Row-Level Security (RLS). RLS policies on tables like `candidates` check `current_setting('app.tenant_id')` and filter rows automatically. Even a direct SQL query from psql would return nothing for the wrong tenant.

The result is an `AuthContext` object passed to every endpoint:

```python
class AuthContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    clerk_user_id: str
```

In tests, the entire dependency is replaced with a fixed stub:

```python
def _auth_override() -> AuthContext:
    return AuthContext(
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        clerk_user_id="test_clerk_user",
    )

app.dependency_overrides[get_current_user] = _auth_override
```

FastAPI's `dependency_overrides` is a dict that swaps any dependency by identity. This removes all JWT validation from test requests.

---

## 7. The API layer (`app/api/candidates.py`)

### POST /candidates — the upload endpoint

```python
@router.post("", status_code=202, response_model=CandidateResponse)
async def upload_candidate(
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    consent_given_at: Annotated[datetime, Form()],
    consent_version: Annotated[str, Form()],
    consent_ip: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CandidateResponse:
```

This is a multipart form upload. `Form()` and `File()` are FastAPI's way of declaring multipart fields — you can't mix JSON body with file upload, so all metadata comes as form fields too.

The endpoint does five things in order:

**Step 1 — Validate the file.**
```python
data = await file.read()
_validate_file(file, data)   # raises 422 for bad extension, 413 for >5MB
```

**Step 2 — Dedup check.**
```python
full_name_hash = hash_field(name)
email_hash = hash_field(email)
existing = await repo.find_by_hashes(ctx.tenant_id, full_name_hash, email_hash)
if existing is not None:
    stat = "processing" if existing.embedding is None else "ready"
    return CandidateResponse(id=existing.id, status=stat, created_at=existing.created_at)
```
If the same name+email combination already exists for this tenant, we return immediately. No file is uploaded, no row is created. The caller gets back the existing candidate's ID and current status. This is idempotent — safe to call multiple times.

**Step 3 — Upload to R2.**
```python
r2_key = await storage.upload_resume(
    str(ctx.tenant_id), str(candidate_id), data, file.filename or "resume"
)
# r2_key = "tenant-uuid/candidate-uuid/filename.pdf"
```

The file is stored in Cloudflare R2 (S3-compatible object storage) before the DB row is created. If the DB insert fails, the file is orphaned in R2, but that is acceptable — a background job cleans orphans (Phase 7). The reverse order (DB first, then R2) would leave a DB row pointing to a non-existent file.

**Step 4 — Create the DB row.**
```python
candidate = await repo.create(
    ctx.tenant_id,
    full_name_hash=full_name_hash,
    email_hash=email_hash,
    contact_jsonb_encrypted=encrypt_bytes(contact_blob),
    resume_text="",           # empty until worker fills it
    structured_jsonb={},      # empty until worker fills it
    r2_key=r2_key,
    consent_given_at=consent_given_at,
    consent_version=consent_version,
)
```

**Step 5 — Dispatch the Celery task and return 202.**
```python
process_resume.delay(str(uuid.uuid4()), str(candidate.id), str(ctx.tenant_id))
return CandidateResponse(id=candidate.id, status="processing", created_at=candidate.created_at)
```

`202 Accepted` means "I received the request and started work, but the work isn't done yet." The client can poll `GET /candidates/{id}` until `status` changes from `"processing"` to `"ready"`.

### DELETE /candidates/{id} — soft delete

```python
@router.delete("/{candidate_id}", status_code=204, response_class=Response)
async def delete_candidate(...) -> Response:
    r2_key = candidate.r2_key
    deleted = await repo.soft_delete(ctx.tenant_id, candidate_id)
    await MatchRepo(db).anonymize_candidate(ctx.tenant_id, candidate_id)
    delete_r2_object.delay(r2_key)
    return Response(status_code=204)
```

Soft delete sets `deleted_at = now()` rather than removing the row. This preserves the audit trail. The R2 deletion is dispatched as a background task — R2 is eventually consistent and we don't need to wait.

`anonymize_candidate` finds any match shortlists that reference this candidate and replaces their `name` and `justification` fields with `"[removed]"`. This is the GDPR erasure cascade.

**FastAPI 204 note:** returning `None` from a 204 endpoint causes a runtime error ("Status code 204 must not have a response body"). You must declare `response_class=Response` on the decorator and explicitly return `Response(status_code=204)`.

---

## 8. The services layer

### `app/services/storage.py` — Cloudflare R2

```python
async def upload_resume(tenant_id: str, candidate_id: str, data: bytes, filename: str) -> str:
    key = f"{tenant_id}/{candidate_id}/{filename}"

    def _upload() -> None:
        _s3_client().put_object(Bucket=settings.r2_bucket_resumes, Key=key, Body=data)

    await asyncio.to_thread(_upload)   # boto3 is sync — run in thread pool
    return key
```

boto3 (AWS SDK) is synchronous. Calling a sync function from an async context would block the event loop — no other requests could run until the upload finished. `asyncio.to_thread` dispatches the sync call to a thread pool worker, freeing the event loop immediately.

All three storage operations (`upload_resume`, `download_resume`, `delete_resume`) use the same pattern.

### `app/services/resume_parser.py` — PDF/DOCX to text

```python
async def extract_text(data: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return await _parse_pdf(data)
    if ext in ("docx", "doc"):
        return await _parse_docx(data)
    raise ValueError(f"Unsupported file type: {ext}")

async def _parse_pdf(data: bytes) -> str:
    def _extract() -> str:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return await asyncio.to_thread(_extract)   # pypdf is CPU-bound
```

Same pattern: CPU-heavy parsing in a thread pool so the event loop stays free.

### `app/services/llm.py` — the LLM gateway

```python
async def chat_completion(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tenant_id: str,
    run_id: str,
    ...
) -> Any:
    response = await litellm.acompletion(model=model, messages=messages, ...)
    logger.info("llm_call model=%s run_id=%s tokens_in=%s tokens_out=%s", ...)
    return response
```

**All LLM calls in the codebase go through this function.** Never `anthropic.Client()`, never `openai.OpenAI()` directly. LiteLLM translates a single uniform interface to whatever provider the `model` string names — `"groq/llama-3.1-8b-instant"`, `"anthropic/claude-sonnet-4-6"`, `"openai/gpt-4o"` all work identically.

This gives you:
- **Provider swapping** without changing call sites
- **Centralised cost logging** (one place to track token usage)
- **Automatic retries and fallbacks** (configured in `litellm.yaml`)

### `app/services/embeddings.py` — OpenAI embeddings

```python
EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIM = 1536

async def embed_text(text: str) -> list[float]:
    response = await litellm.aembedding(model=EMBEDDING_MODEL, input=[text])
    return response.data[0]["embedding"]
```

Returns a list of 1536 floats — a dense vector encoding the semantic meaning of the text. Candidates with similar skills/experience end up with vectors that are close together in this 1536-dimensional space. The vector is stored in Postgres via pgvector and indexed for fast cosine similarity search (Phase 3).

---

## 9. The ingest worker — the heart of Phase 2

### Why a background worker?

Uploading → parsing → embedding takes 2–10 seconds. Doing it synchronously in the HTTP request would:
- Hold a DB connection open the whole time (expensive)
- Make the client wait 10s for a 202 response (confusing)
- Fail unrecoverably if Groq times out mid-request

A Celery task decouples the slow work. The HTTP response is immediate; the client polls for completion.

### Celery setup (`app/workers/celery_app.py`)

```python
celery_app = Celery(
    "jobmatch",
    broker=settings.redis_url,    # Redis as message queue
    backend=settings.redis_url,   # Redis stores task results
    include=["app.workers.ingest_worker", ...],
)
celery_app.conf.update(
    task_acks_late=True,          # acknowledge only after task completes
    worker_prefetch_multiplier=1, # don't grab more tasks than you can handle
)
```

`task_acks_late=True` is important for reliability: if the worker crashes mid-task, the task stays in the queue and another worker picks it up.

### The task definition (`app/workers/ingest_worker.py`)

```python
@celery_app.task(name="ingest.process_resume", bind=True)
def process_resume(self: object, run_id: str, candidate_id: str, tenant_id: str) -> None:
    asyncio.run(_run_process_resume(run_id, candidate_id, tenant_id))
```

Celery workers are synchronous by default. `asyncio.run()` creates a new event loop, runs the async function to completion, and tears it down. The async core logic lives in `_run_process_resume` — keeping it separate from the Celery wrapper makes it directly testable without Celery.

### The core async function

```python
async def _run_process_resume(run_id: str, candidate_id: str, tenant_id: str) -> None:
    candidate_uuid = uuid.UUID(candidate_id)
    tenant_uuid = uuid.UUID(tenant_id)

    # Step 1: Fetch candidate from DB
    async with AsyncSessionLocal() as session:
        async with session.begin():
            candidate = await CandidateRepo(session).get(tenant_uuid, candidate_uuid)

    if candidate is None:
        logger.warning("ingest_worker candidate_not_found candidate_id=%s", candidate_id)
        return

    # Step 2: Idempotency guard
    if candidate.embedding is not None:
        logger.info("ingest_worker already_parsed candidate_id=%s", candidate_id)
        return

    # Step 3: Download file from R2
    filename = candidate.r2_key.rsplit("/", 1)[-1]
    data = await storage.download_resume(candidate.r2_key)

    # Step 4: Extract text
    resume_text = await resume_parser.extract_text(data, filename)

    # Step 5: Parse structured fields with Groq
    try:
        parsed = await _parse_with_groq(resume_text, run_id, tenant_id)
        structured = parsed.model_dump()
        phone, address = parsed.phone, parsed.address
    except Exception as exc:
        logger.error("ingest_worker parse_error candidate_id=%s error=%s", candidate_id, exc)
        structured, phone, address = {}, None, None   # degrade gracefully

    # Step 6: Embed the resume text
    embedding = await embeddings.embed_text(resume_text)

    # Step 7: Merge phone/address into the contact blob
    contact = json.loads(decrypt_bytes(candidate.contact_jsonb_encrypted))
    contact["phone"] = phone
    contact["address"] = address
    new_contact_encrypted = encrypt_bytes(json.dumps(contact).encode())

    # Step 8: Persist everything
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await CandidateRepo(session).update_after_parse(
                tenant_uuid, candidate_uuid,
                resume_text=resume_text,
                structured_jsonb=structured,
                embedding=embedding,
                contact_jsonb_encrypted=new_contact_encrypted,
            )
            await AuditRepo(session).log(
                tenant_uuid, "resume_parsed",
                entity_type="candidate",
                entity_id=candidate_uuid,
                payload={"run_id": run_id},
            )
```

**Why two DB sessions?** The first session (fetch) is closed before the slow network/CPU work begins. Holding a DB connection open during a 5-second Groq call and a 1-second embedding call would exhaust the connection pool under load.

**Why does parse failure not stop the worker?** If Groq times out, `structured_jsonb` is stored as `{}` and phone/address remain null — but the embedding still runs and the candidate becomes `ready`. An empty `structured_jsonb` just means the candidate won't match structured filters (skills, seniority), but they'll still appear in vector similarity searches. Better to be partially searchable than stuck in `processing` indefinitely.

### How Groq parsing works

```python
async def _parse_with_groq(resume_text: str, run_id: str, tenant_id: str) -> ParsedResume:
    raw = await _call_groq(resume_text, run_id, tenant_id)
    return ParsedResume(**json.loads(raw))
```

`_call_groq` injects the resume text into the prompt template loaded from `app/services/prompts/parse_resume.txt`, sends it to `groq/llama-3.1-8b-instant`, and returns the raw string response.

The prompt instructs the model to return only valid JSON matching the `ParsedResume` schema. `json.loads` then `ParsedResume(**data)` validates the output — if the model hallucinated an invalid `seniority_level` value, Pydantic raises `ValidationError` and the fallback kicks in.

```python
class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    phone: str | None
    address: str | None
    # ...
```

`Literal["junior", "mid", ...]` means Pydantic will reject any value not in that set. This is the validation boundary — untrusted LLM output becomes a typed Python object.

---

## 10. The testing strategy

The project has three distinct test layers. Understanding why each layer exists helps you know which to write for a new feature.

### Unit tests (`tests/unit/`)

Test a single pure function with no external dependencies.

```python
# tests/unit/test_candidate_hashing.py
def test_whitespace_is_stripped():
    assert hash_field("  Jane ") == hash_field("Jane")

def test_case_is_normalised():
    assert hash_field("JANE@EXAMPLE.COM") == hash_field("jane@example.com")
```

No fixtures, no mocks, no DB. Run instantly. Write these for every utility function.

### Contract tests (`tests/contract/`)

Test a single module with all external I/O mocked. Verify the *contract* — given this input shape, do we call the right things with the right arguments?

```python
# tests/contract/test_ingest_worker.py
@pytest.mark.asyncio
async def test_process_resume_happy_path() -> None:
    candidate = _make_candidate(already_parsed=False)
    candidate_repo = AsyncMock()
    candidate_repo.get.return_value = candidate

    with (
        patch("app.workers.ingest_worker.AsyncSessionLocal", session_factory),
        patch("app.workers.ingest_worker.CandidateRepo", return_value=candidate_repo),
        patch("app.workers.ingest_worker.storage.download_resume", AsyncMock(...)),
        patch("app.workers.ingest_worker.llm.chat_completion", AsyncMock(...)),
        patch("app.workers.ingest_worker.embeddings.embed_text", AsyncMock(...)),
        patch("app.workers.ingest_worker.decrypt_bytes", lambda b: b),  # bypass Fernet
        patch("app.workers.ingest_worker.encrypt_bytes", lambda b: b),
    ):
        await _run_process_resume(RUN_ID, CANDIDATE_ID, TENANT_ID)

    candidate_repo.update_after_parse.assert_awaited_once()
```

**Why patch at the module path, not the source?** `patch("app.workers.ingest_worker.storage.download_resume")` replaces the `storage` object *as seen by the ingest_worker module*. If you patched `app.services.storage.download_resume`, the ingest_worker's already-imported reference would be unaffected.

**Why mock `decrypt_bytes` / `encrypt_bytes`?** Fernet generates a random throwaway key each call when `DB_ENCRYPTION_KEY` is not set. Encrypting in `_make_candidate()` and decrypting in the worker would use two different random keys and fail. The test doesn't need to verify encryption correctness — a separate unit test covers that. Mock it out and test what you're actually testing.

**Why test idempotency?**

```python
async def test_process_resume_idempotent() -> None:
    candidate = _make_candidate(already_parsed=True)   # embedding already set
    download_mock = AsyncMock()

    with (...):
        await _run_process_resume(RUN_ID, CANDIDATE_ID, TENANT_ID)

    download_mock.assert_not_awaited()   # nothing should have happened
```

If a task is retried (worker crash, network blip), running it twice must not double-embed or double-write. The idempotency guard (`if candidate.embedding is not None: return`) is a safety invariant, so it has a dedicated test.

### Integration tests (`tests/integration/`)

Test the full HTTP stack against a real Postgres instance. These are the closest thing to "does the feature actually work?"

```python
async def test_upload_new_candidate_returns_202(client: AsyncClient, unique_form) -> None:
    with patch("app.workers.ingest_worker.process_resume") as mock_task:
        mock_task.delay.return_value = None   # don't actually run Celery
        response = await client.post(
            "/candidates",
            data=unique_form,
            files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
```

The `client` fixture (`tests/conftest.py`) runs the FastAPI app in-process using httpx's `ASGITransport` — no network, but the full request/response cycle including middleware and dependency injection. It depends on `setup_test_tenant` which seeds a tenant and user into a real DB before any test runs.

These tests currently fail with `ConnectionRefusedError` because Postgres isn't running locally. They pass when you run `docker compose up -d postgres redis` first.

---

## 11. End-to-end flow: what happens when a recruiter uploads a resume

```
Recruiter POSTs to /candidates
        │
        ├── FastAPI reads multipart form fields
        ├── _validate_file() → 422 if bad ext, 413 if >5MB
        ├── hash_field(name) + hash_field(email)
        ├── CandidateRepo.find_by_hashes() → 200 if duplicate
        ├── storage.upload_resume() → R2 key stored
        ├── CandidateRepo.create() → DB row, embedding=NULL
        ├── process_resume.delay() → message pushed to Redis
        └── return 202 {"status": "processing"}

Redis queue
        │
        └── Celery worker picks up message
                ├── CandidateRepo.get() → fetch row + r2_key
                ├── storage.download_resume() → raw PDF bytes
                ├── resume_parser.extract_text() → plain text string
                ├── _parse_with_groq() → ParsedResume (Pydantic-validated)
                ├── embeddings.embed_text() → list[float] (1536 dims)
                ├── decrypt_bytes() → merge phone/address into contact blob
                ├── encrypt_bytes() → re-encrypt contact blob
                └── CandidateRepo.update_after_parse()
                        ├── resume_text = full text
                        ├── structured_jsonb = parsed fields
                        ├── embedding = vector
                        └── contact_jsonb_encrypted = updated blob

Recruiter GETs /candidates/{id}
        └── CandidateRepo.get_status()
                └── embedding IS NOT NULL → {"status": "ready"}
```

---

## 12. Running it locally

```bash
# Start Postgres + Redis
docker compose up -d postgres redis

# Apply migrations
uv run alembic upgrade head

# Start API + worker (two terminals)
uv run uvicorn app.main:app --reload
uv run celery -A app.workers.celery_app worker --loglevel=info

# Run tests (unit + contract — no DB needed)
uv run pytest tests/unit/ tests/contract/ -v

# Run all tests (needs DB)
uv run pytest -v

# Run with coverage
uv run pytest tests/unit/ tests/contract/ --cov=app --cov-report=term-missing
```

Required env vars for a full run (copy `.env.example` to `.env` and fill in):

| Variable | Where to get it |
|---|---|
| `OPENAI_API_KEY` | platform.openai.com |
| `GROQ_API_KEY` | console.groq.com |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare dashboard |
| `DB_ENCRYPTION_KEY` | `python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `CLERK_SECRET_KEY` | clerk.com dashboard |

`ANTHROPIC_API_KEY` is not needed for Phase 2 — Groq and OpenAI embeddings are sufficient. Claude Sonnet is used in Phase 4 (the agent).
