# Phase 2 — Ingest Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the resume upload and async processing pipeline — POST/GET/DELETE /candidates API endpoints plus the Celery ingest worker that parses and embeds resumes.

**Architecture:** The API endpoint accepts a multipart form (name, email, consent fields, file), deduplicates synchronously via SHA-256 hashes, uploads the raw file to Cloudflare R2, creates a stub DB row, and dispatches a Celery task. The worker downloads the file, extracts text, calls Groq for structured JSON parsing, embeds via OpenAI, and updates the row. DELETE soft-deletes the row, anonymizes related matches, and dispatches an async R2 cleanup task.

**Tech stack:** FastAPI, SQLAlchemy 2.0 async, Celery + Redis, LiteLLM → Groq `llama-3.1-8b-instant` + OpenAI `text-embedding-3-small`, Cloudflare R2 (boto3), Fernet encryption, pytest + pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/db/migrations/versions/0010_add_consent_version.py` | Create | Add `consent_version` column to `candidates` |
| `app/db/models.py` | Modify | Add `consent_version` field to `Candidate` model |
| `app/core/security.py` | Modify | Add `hash_field()` for dedup hashing |
| `app/db/repos.py` | Modify | Update `CandidateRepo.create`; add `update_after_parse`, `get_status`; add `MatchRepo.anonymize_candidate` |
| `app/services/prompts/__init__.py` | Create | Package marker so `importlib.resources` can find prompt files |
| `app/services/prompts/parse_resume.txt` | Create | Groq structured-parse prompt |
| `app/workers/ingest_worker.py` | Modify | `ParsedResume` schema + `process_resume` + `delete_r2_object` tasks |
| `app/api/candidates.py` | Modify | POST, GET, DELETE endpoints + `CandidateResponse` model |
| `tests/conftest.py` | Modify | Session-scoped tenant seed; `unique_form` fixture; auth override |
| `tests/unit/test_candidate_hashing.py` | Create | `hash_field` unit tests |
| `tests/unit/test_parsed_resume_schema.py` | Create | `ParsedResume` schema tests |
| `tests/contract/test_ingest_worker.py` | Create | Worker contract tests (mocked I/O) |
| `tests/integration/test_candidates_api.py` | Create | Full API integration tests against real Postgres |
| `tests/adversarial/test_candidate_upload.py` | Create | Cross-tenant, oversized file, bad extension, bad timestamp |

---

## Task 1: DB migration + model update

**Files:**
- Create: `app/db/migrations/versions/0010_add_consent_version.py`
- Modify: `app/db/models.py`

- [ ] **Step 1: Create the migration**

```python
# app/db/migrations/versions/0010_add_consent_version.py
"""add_consent_version_to_candidates

Revision ID: 0010
Revises: 0002
Create Date: 2026-04-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("consent_version", sa.Text(), nullable=False, server_default="v1"),
    )


def downgrade() -> None:
    op.drop_column("candidates", "consent_version")
```

- [ ] **Step 2: Add the column to the Candidate model**

In `app/db/models.py`, inside the `Candidate` class, add after the `consent_given_at` line:

```python
    consent_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="v1")
```

- [ ] **Step 3: Run the migration**

```bash
docker compose up -d postgres redis
alembic upgrade head
```

Expected: `Running upgrade 0002 -> 0010, add_consent_version_to_candidates`

- [ ] **Step 4: Verify column exists**

```bash
docker compose exec postgres psql -U jobmatch -d jobmatch -c "\d candidates" | grep consent
```

Expected output includes both `consent_given_at` and `consent_version`.

- [ ] **Step 5: Commit**

```bash
git add app/db/migrations/versions/0010_add_consent_version.py app/db/models.py
git commit -m "feat: add consent_version column to candidates"
```

---

## Task 2: Hash utility (TDD)

**Files:**
- Create: `tests/unit/test_candidate_hashing.py`
- Modify: `app/core/security.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_candidate_hashing.py
from app.core.security import hash_field


def test_hash_normalizes_whitespace():
    assert hash_field("Jane Doe") == hash_field("  Jane Doe  ")


def test_hash_normalizes_case():
    assert hash_field("Jane Doe") == hash_field("JANE DOE")
    assert hash_field("jane@example.com") == hash_field("JANE@EXAMPLE.COM")


def test_hash_different_values_differ():
    assert hash_field("Jane Doe") != hash_field("John Doe")
    assert hash_field("a@b.com") != hash_field("a@c.com")


def test_hash_returns_64_char_hex():
    result = hash_field("test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_candidate_hashing.py -v
```

Expected: `ImportError` — `hash_field` does not exist yet.

- [ ] **Step 3: Implement hash_field**

Add `import hashlib` at the top of `app/core/security.py`, then add at the end of the file:

```python
def hash_field(value: str) -> str:
    """SHA-256 of a normalized string (strip + lowercase). Used for candidate dedup."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()
```

- [ ] **Step 4: Run to confirm all pass**

```bash
pytest tests/unit/test_candidate_hashing.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/unit/test_candidate_hashing.py
git commit -m "feat: add hash_field for candidate dedup"
```

---

## Task 3: ParsedResume schema (TDD)

**Files:**
- Create: `tests/unit/test_parsed_resume_schema.py`
- Modify: `app/workers/ingest_worker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_parsed_resume_schema.py
import pytest
from pydantic import ValidationError

from app.workers.ingest_worker import ParsedResume


def test_valid_minimal():
    data = {
        "skills": [],
        "experience_years": None,
        "education": [],
        "languages": [],
        "location": None,
        "seniority_level": None,
        "job_titles": [],
        "certifications": [],
        "phone": None,
        "address": None,
    }
    parsed = ParsedResume.model_validate(data)
    assert parsed.skills == []
    assert parsed.experience_years is None


def test_valid_full():
    data = {
        "skills": ["Python", "SQL"],
        "experience_years": 5,
        "education": [{"degree": "BSc", "institution": "MIT", "year": 2018}],
        "languages": ["English", "French"],
        "location": "Paris, FR",
        "seniority_level": "senior",
        "job_titles": ["Software Engineer"],
        "certifications": ["AWS SAA"],
        "phone": "+33612345678",
        "address": "12 Rue de la Paix, Paris",
    }
    parsed = ParsedResume.model_validate(data)
    assert parsed.seniority_level == "senior"
    assert parsed.education[0].degree == "BSc"
    assert parsed.education[0].year == 2018


def test_invalid_seniority_raises():
    data = {
        "skills": [],
        "experience_years": None,
        "education": [],
        "languages": [],
        "location": None,
        "seniority_level": "god-tier",
        "job_titles": [],
        "certifications": [],
        "phone": None,
        "address": None,
    }
    with pytest.raises(ValidationError):
        ParsedResume.model_validate(data)


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        ParsedResume.model_validate({"skills": ["Python"]})
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_parsed_resume_schema.py -v
```

Expected: `ImportError` — `ParsedResume` not defined yet.

- [ ] **Step 3: Add ParsedResume to ingest_worker.py**

Replace the full contents of `app/workers/ingest_worker.py` with:

```python
"""Resume ingest worker — parse + embed on upload."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: int | None = None


class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    languages: list[str]
    location: str | None
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    job_titles: list[str]
    certifications: list[str]
    phone: str | None
    address: str | None


@celery_app.task(name="ingest.process_resume", bind=True)
def process_resume(self: object, run_id: str, candidate_id: str, tenant_id: str) -> None:
    raise NotImplementedError("Implemented in Task 9")


@celery_app.task(name="ingest.delete_r2_object", bind=True)
def delete_r2_object(self: object, r2_key: str) -> None:
    raise NotImplementedError("Implemented in Task 10")
```

- [ ] **Step 4: Run to confirm all pass**

```bash
pytest tests/unit/test_parsed_resume_schema.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/workers/ingest_worker.py tests/unit/test_parsed_resume_schema.py
git commit -m "feat: add ParsedResume Pydantic schema to ingest worker"
```

---

## Task 4: Groq parse prompt

**Files:**
- Create: `app/services/prompts/__init__.py`
- Create: `app/services/prompts/parse_resume.txt`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p app/services/prompts
touch app/services/prompts/__init__.py
```

- [ ] **Step 2: Write the prompt file**

Create `app/services/prompts/parse_resume.txt` with this exact content:

```
You are a resume parser. Extract structured information from the resume text below.

Return ONLY valid JSON matching this exact schema. No prose, no markdown fences, no explanation — raw JSON only.

{
  "skills": ["list of technical and soft skills as strings"],
  "experience_years": <integer total years of professional experience, or null if unclear>,
  "education": [
    {"degree": "<degree name>", "institution": "<institution name>", "year": <graduation year as integer or null>}
  ],
  "languages": ["list of spoken or written languages as strings"],
  "location": "<city and country as a string, or null if not found>",
  "seniority_level": "<exactly one of: junior, mid, senior, lead, executive — or null if unclear>",
  "job_titles": ["list of all past and current job titles as strings"],
  "certifications": ["list of certifications and licences as strings"],
  "phone": "<phone number as a string, or null if not found>",
  "address": "<full postal address as a string, or null if not found>"
}

Rules:
- experience_years must be an integer (total professional years), not a string. Sum up all roles if needed.
- seniority_level must be exactly one of: junior, mid, senior, lead, executive, or null. Do not use any other value.
- All list fields default to [] if nothing is found.
- All nullable scalar fields default to null if not found.
- Do not invent information not present in the resume.
- Do not include any text before or after the JSON object.

RESUME TEXT:
---
{resume_text}
---
```

- [ ] **Step 3: Verify importlib.resources can find it**

```bash
python -c "
import importlib.resources
ref = importlib.resources.files('app.services.prompts').joinpath('parse_resume.txt')
print(ref.read_text(encoding='utf-8')[:40])
"
```

Expected: first 40 chars of the prompt printed without error.

- [ ] **Step 4: Commit**

```bash
git add app/services/prompts/__init__.py app/services/prompts/parse_resume.txt
git commit -m "feat: add Groq resume parse prompt"
```

---

## Task 5: Repo additions

**Files:**
- Modify: `app/db/repos.py`

- [ ] **Step 1: Update CandidateRepo.create to include consent_version**

In `app/db/repos.py`, replace the `create` method of `CandidateRepo` with:

```python
    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        full_name_hash: str,
        email_hash: str,
        contact_jsonb_encrypted: bytes,
        resume_text: str,
        structured_jsonb: dict[str, Any],
        r2_key: str,
        consent_given_at: datetime,
        consent_version: str,
    ) -> Candidate:
        candidate = Candidate(
            tenant_id=tenant_id,
            full_name_hash=full_name_hash,
            email_hash=email_hash,
            contact_jsonb_encrypted=contact_jsonb_encrypted,
            resume_text=resume_text,
            structured_jsonb=structured_jsonb,
            r2_key=r2_key,
            consent_given_at=consent_given_at,
            consent_version=consent_version,
        )
        self._db.add(candidate)
        await self._db.flush()
        return candidate
```

- [ ] **Step 2: Add update_after_parse and get_status to CandidateRepo**

Add these two methods after `soft_delete` in `CandidateRepo`:

```python
    async def update_after_parse(
        self,
        tenant_id: uuid.UUID,
        candidate_id: uuid.UUID,
        *,
        resume_text: str,
        structured_jsonb: dict[str, Any],
        embedding: list[float],
        contact_jsonb_encrypted: bytes,
    ) -> None:
        await self._db.execute(
            update(Candidate)
            .where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
            )
            .values(
                resume_text=resume_text,
                structured_jsonb=structured_jsonb,
                embedding=embedding,
                contact_jsonb_encrypted=contact_jsonb_encrypted,
                updated_at=datetime.now(UTC),
            )
        )

    async def get_status(
        self, tenant_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> dict[str, Any] | None:
        result = await self._db.execute(
            select(
                Candidate.id,
                Candidate.embedding,
                Candidate.created_at,
            ).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
                Candidate.deleted_at.is_(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "status": "processing" if row.embedding is None else "ready",
            "created_at": row.created_at,
        }
```

- [ ] **Step 3: Add anonymize_candidate to MatchRepo**

Add this method after `update_status` in `MatchRepo`:

```python
    async def anonymize_candidate(
        self, tenant_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> None:
        result = await self._db.execute(
            select(Match.id, Match.shortlist_jsonb).where(
                Match.tenant_id == tenant_id,
                Match.shortlist_jsonb.isnot(None),
            )
        )
        rows = result.all()
        for row in rows:
            shortlist = row.shortlist_jsonb
            if not isinstance(shortlist, list):
                continue
            changed = False
            for entry in shortlist:
                if str(entry.get("candidate_id")) == str(candidate_id):
                    entry["name"] = "[removed]"
                    entry["justification"] = "[removed]"
                    changed = True
            if changed:
                await self._db.execute(
                    update(Match)
                    .where(Match.id == row.id)
                    .values(shortlist_jsonb=shortlist)
                )
```

- [ ] **Step 4: Confirm clean import**

```bash
python -c "from app.db.repos import CandidateRepo, MatchRepo; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add app/db/repos.py
git commit -m "feat: add update_after_parse, get_status, anonymize_candidate to repos"
```

---

## Task 6: Test fixtures

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Replace conftest.py with extended fixtures**

```python
# tests/conftest.py
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import AuthContext, get_current_user
from app.db.session import AsyncSessionLocal
from app.main import app

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _auth_override() -> AuthContext:
    return AuthContext(
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        clerk_user_id="test_clerk_user",
    )


@pytest.fixture(scope="session")
async def setup_test_tenant() -> None:
    """Seed a tenant + user once per test session. Committed so API sessions see it."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, clerk_org_id) "
                    "VALUES (:id, :name, :org) ON CONFLICT DO NOTHING"
                ),
                {"id": str(TEST_TENANT_ID), "name": "Test Co", "org": "org_test"},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, clerk_user_id, email) "
                    "VALUES (:id, :tid, :uid, :email) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(TEST_USER_ID),
                    "tid": str(TEST_TENANT_ID),
                    "uid": "test_clerk_user",
                    "email": "recruiter@test.com",
                },
            )


@pytest.fixture
async def client(setup_test_tenant: None) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_current_user] = _auth_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def unique_form() -> dict[str, str]:
    """Unique name + email per test to avoid dedup collisions between tests."""
    uid = uuid.uuid4().hex[:8]
    return {
        "name": f"Jane {uid}",
        "email": f"jane-{uid}@example.com",
        "consent_given_at": "2026-04-24T10:00:00Z",
        "consent_version": "v1",
        "consent_ip": "1.2.3.4",
    }
```

- [ ] **Step 2: Confirm existing unit tests still pass**

```bash
pytest tests/unit/ -v
```

Expected: all unit tests PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add session-scoped tenant seed and unique_form fixture"
```

---

## Task 7: POST /candidates endpoint (TDD)

**Files:**
- Modify: `app/api/candidates.py`
- Create: `tests/integration/test_candidates_api.py`

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_candidates_api.py
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest


def _pdf(content: bytes = b"%PDF-1.4 stub") -> dict:
    return {"file": ("resume.pdf", io.BytesIO(content), "application/pdf")}


@pytest.mark.asyncio
async def test_post_candidate_returns_202(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        resp = await client.post("/candidates", data=unique_form, files=_pdf())
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "processing"
    uuid.UUID(body["id"])  # valid UUID


@pytest.mark.asyncio
async def test_post_candidate_dedup_returns_200(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        r1 = await client.post("/candidates", data=unique_form, files=_pdf())
        r2 = await client.post("/candidates", data=unique_form, files=_pdf())
    assert r1.status_code == 202
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/integration/test_candidates_api.py -v
```

Expected: tests fail (endpoint returns empty router / 404 or 422).

- [ ] **Step 3: Implement POST /candidates**

Replace the full contents of `app/api/candidates.py`:

```python
"""Candidate endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.core.security import encrypt_bytes, hash_field
from app.db.repos import AuditRepo, CandidateRepo, MatchRepo
from app.db.session import get_db
from app.services import storage
from app.workers.ingest_worker import delete_r2_object, process_resume

router = APIRouter(prefix="/candidates", tags=["candidates"])

_MAX_FILE_BYTES = 5 * 1024 * 1024
_ALLOWED_EXTENSIONS = {"pdf", "docx", "doc"}


class CandidateResponse(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime


def _validate_file(file: UploadFile, data: bytes) -> None:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: .{ext}")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 5 MB limit")


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
    data = await file.read()
    _validate_file(file, data)

    full_name_hash = hash_field(name)
    email_hash = hash_field(email)
    repo = CandidateRepo(db)

    existing = await repo.find_by_hashes(ctx.tenant_id, full_name_hash, email_hash)
    if existing is not None:
        stat = "processing" if existing.embedding is None else "ready"
        return CandidateResponse(
            id=existing.id, status=stat, created_at=existing.created_at
        )

    contact_blob = json.dumps({
        "name": name.strip(),
        "email": email.strip().lower(),
        "consent_ip": consent_ip,
        "phone": None,
        "address": None,
    }).encode()

    candidate_id = uuid.uuid4()
    r2_key = await storage.upload_resume(
        str(ctx.tenant_id), str(candidate_id), data, file.filename or "resume"
    )

    candidate = await repo.create(
        ctx.tenant_id,
        full_name_hash=full_name_hash,
        email_hash=email_hash,
        contact_jsonb_encrypted=encrypt_bytes(contact_blob),
        resume_text="",
        structured_jsonb={},
        r2_key=r2_key,
        consent_given_at=consent_given_at,
        consent_version=consent_version,
    )

    process_resume.delay(str(uuid.uuid4()), str(candidate.id), str(ctx.tenant_id))

    await AuditRepo(db).log(
        ctx.tenant_id,
        "candidate_uploaded",
        actor_id=ctx.user_id,
        entity_type="candidate",
        entity_id=candidate.id,
    )

    return CandidateResponse(
        id=candidate.id, status="processing", created_at=candidate.created_at
    )
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
pytest tests/integration/test_candidates_api.py::test_post_candidate_returns_202 \
       tests/integration/test_candidates_api.py::test_post_candidate_dedup_returns_200 -v
```

Expected: 2 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/api/candidates.py tests/integration/test_candidates_api.py
git commit -m "feat: implement POST /candidates with dedup and Celery dispatch"
```

---

## Task 8: GET /candidates/{id} endpoint (TDD)

**Files:**
- Modify: `app/api/candidates.py`
- Modify: `tests/integration/test_candidates_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_candidates_api.py`:

```python
@pytest.mark.asyncio
async def test_get_candidate_processing(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        post = await client.post("/candidates", data=unique_form, files=_pdf())
    cid = post.json()["id"]

    resp = await client.get(f"/candidates/{cid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    assert resp.json()["id"] == cid


@pytest.mark.asyncio
async def test_get_candidate_not_found(client):
    resp = await client.get(f"/candidates/{uuid.uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/integration/test_candidates_api.py::test_get_candidate_processing -v
```

Expected: `404 Not Found` (route not defined).

- [ ] **Step 3: Add GET endpoint**

Add after `upload_candidate` in `app/api/candidates.py`:

```python
@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate(
    candidate_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CandidateResponse:
    info = await CandidateRepo(db).get_status(ctx.tenant_id, candidate_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return CandidateResponse(**info)
```

- [ ] **Step 4: Run to confirm all integration tests pass**

```bash
pytest tests/integration/test_candidates_api.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/api/candidates.py tests/integration/test_candidates_api.py
git commit -m "feat: implement GET /candidates/{id}"
```

---

## Task 9: DELETE /candidates/{id} endpoint (TDD)

**Files:**
- Modify: `app/api/candidates.py`
- Modify: `tests/integration/test_candidates_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_candidates_api.py`:

```python
@pytest.mark.asyncio
async def test_delete_candidate_returns_204(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        post = await client.post("/candidates", data=unique_form, files=_pdf())
    cid = post.json()["id"]

    with patch("app.api.candidates.delete_r2_object") as mock_del:
        mock_del.delay = lambda *a, **kw: None
        resp = await client.delete(f"/candidates/{cid}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_after_delete_is_404(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        post = await client.post("/candidates", data=unique_form, files=_pdf())
    cid = post.json()["id"]

    with patch("app.api.candidates.delete_r2_object") as mock_del:
        mock_del.delay = lambda *a, **kw: None
        await client.delete(f"/candidates/{cid}")

    resp = await client.get(f"/candidates/{cid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(client):
    resp = await client.delete(f"/candidates/{uuid.uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/integration/test_candidates_api.py::test_delete_candidate_returns_204 -v
```

Expected: `405 Method Not Allowed` (DELETE not defined).

- [ ] **Step 3: Add DELETE endpoint**

Add after `get_candidate` in `app/api/candidates.py`:

```python
@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    candidate_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    repo = CandidateRepo(db)
    candidate = await repo.get(ctx.tenant_id, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    r2_key = candidate.r2_key
    deleted = await repo.soft_delete(ctx.tenant_id, candidate_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Candidate not found")

    await MatchRepo(db).anonymize_candidate(ctx.tenant_id, candidate_id)
    delete_r2_object.delay(r2_key)

    await AuditRepo(db).log(
        ctx.tenant_id,
        "candidate_deleted",
        actor_id=ctx.user_id,
        entity_type="candidate",
        entity_id=candidate_id,
    )
```

- [ ] **Step 4: Run all integration tests**

```bash
pytest tests/integration/test_candidates_api.py -v
```

Expected: 7 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/api/candidates.py tests/integration/test_candidates_api.py
git commit -m "feat: implement DELETE /candidates/{id} with soft delete and R2 cleanup"
```

---

## Task 10: ingest_worker.process_resume (TDD)

**Files:**
- Create: `tests/contract/test_ingest_worker.py`
- Modify: `app/workers/ingest_worker.py`

- [ ] **Step 1: Write the failing contract tests**

```python
# tests/contract/test_ingest_worker.py
"""Contract: process_resume state-in -> state-out, fully mocked I/O."""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.ingest_worker import _run_process_resume

TENANT_ID = str(uuid.uuid4())
CANDIDATE_ID = str(uuid.uuid4())

_CONTACT = json.dumps({
    "name": "Jane Doe",
    "email": "jane@example.com",
    "consent_ip": "1.2.3.4",
    "phone": None,
    "address": None,
}).encode()

_PARSED_JSON = json.dumps({
    "skills": ["Python"],
    "experience_years": 5,
    "education": [{"degree": "BSc", "institution": "MIT", "year": 2020}],
    "languages": ["English"],
    "location": "Paris, FR",
    "seniority_level": "senior",
    "job_titles": ["Engineer"],
    "certifications": [],
    "phone": "+33600000000",
    "address": "Paris",
})


def _candidate(resume_text: str = "") -> MagicMock:
    c = MagicMock()
    c.id = uuid.UUID(CANDIDATE_ID)
    c.tenant_id = uuid.UUID(TENANT_ID)
    c.r2_key = f"{TENANT_ID}/{CANDIDATE_ID}/resume.pdf"
    c.resume_text = resume_text
    c.contact_jsonb_encrypted = b"encrypted"
    return c


def _make_session_mock() -> MagicMock:
    """Build a mock AsyncSessionLocal whose context manager yields a mock DB session."""
    mock_db = AsyncMock()

    @asynccontextmanager
    async def _begin():
        yield

    mock_db.begin = _begin
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_session)


@pytest.mark.asyncio
async def test_process_resume_updates_row():
    mock_repo = AsyncMock()
    mock_repo.get.return_value = _candidate()
    mock_audit = AsyncMock()

    with patch("app.workers.ingest_worker.storage.download_resume", new_callable=AsyncMock, return_value=b"%PDF"), \
         patch("app.workers.ingest_worker.resume_parser.extract_text", new_callable=AsyncMock, return_value="Jane Doe resume text"), \
         patch("app.workers.ingest_worker._call_groq", new_callable=AsyncMock, return_value=_PARSED_JSON), \
         patch("app.workers.ingest_worker.embeddings.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1536), \
         patch("app.workers.ingest_worker.decrypt_bytes", return_value=_CONTACT), \
         patch("app.workers.ingest_worker.encrypt_bytes", return_value=b"re-enc"), \
         patch("app.workers.ingest_worker.CandidateRepo", return_value=mock_repo), \
         patch("app.workers.ingest_worker.AuditRepo", return_value=mock_audit), \
         patch("app.workers.ingest_worker.AsyncSessionLocal", _make_session_mock()):

        await _run_process_resume("run-1", CANDIDATE_ID, TENANT_ID)

    mock_repo.update_after_parse.assert_called_once()
    kw = mock_repo.update_after_parse.call_args.kwargs
    assert kw["resume_text"] == "Jane Doe resume text"
    assert kw["structured_jsonb"]["skills"] == ["Python"]
    assert kw["structured_jsonb"]["seniority_level"] == "senior"
    assert len(kw["embedding"]) == 1536


@pytest.mark.asyncio
async def test_process_resume_idempotent():
    mock_repo = AsyncMock()
    mock_repo.get.return_value = _candidate(resume_text="already done")
    mock_audit = AsyncMock()

    with patch("app.workers.ingest_worker.storage.download_resume", new_callable=AsyncMock) as mock_dl, \
         patch("app.workers.ingest_worker.CandidateRepo", return_value=mock_repo), \
         patch("app.workers.ingest_worker.AuditRepo", return_value=mock_audit), \
         patch("app.workers.ingest_worker.AsyncSessionLocal", _make_session_mock()):

        await _run_process_resume("run-1", CANDIDATE_ID, TENANT_ID)

    mock_dl.assert_not_called()
    mock_repo.update_after_parse.assert_not_called()


@pytest.mark.asyncio
async def test_process_resume_parse_error_persisted():
    """When Groq returns unparseable JSON twice, persist parse_error=True."""
    mock_repo = AsyncMock()
    mock_repo.get.return_value = _candidate()
    mock_audit = AsyncMock()

    with patch("app.workers.ingest_worker.storage.download_resume", new_callable=AsyncMock, return_value=b"%PDF"), \
         patch("app.workers.ingest_worker.resume_parser.extract_text", new_callable=AsyncMock, return_value="garbage"), \
         patch("app.workers.ingest_worker._call_groq", new_callable=AsyncMock, return_value="not json at all"), \
         patch("app.workers.ingest_worker.embeddings.embed_text", new_callable=AsyncMock, return_value=[0.0] * 1536), \
         patch("app.workers.ingest_worker.decrypt_bytes", return_value=_CONTACT), \
         patch("app.workers.ingest_worker.encrypt_bytes", return_value=b"re-enc"), \
         patch("app.workers.ingest_worker.CandidateRepo", return_value=mock_repo), \
         patch("app.workers.ingest_worker.AuditRepo", return_value=mock_audit), \
         patch("app.workers.ingest_worker.AsyncSessionLocal", _make_session_mock()):

        await _run_process_resume("run-1", CANDIDATE_ID, TENANT_ID)

    kw = mock_repo.update_after_parse.call_args.kwargs
    assert kw["structured_jsonb"] == {"parse_error": True}
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/contract/test_ingest_worker.py -v
```

Expected: `ImportError: cannot import name '_run_process_resume'`

- [ ] **Step 3: Implement process_resume**

Replace the full contents of `app/workers/ingest_worker.py`:

```python
"""Resume ingest worker — parse + embed on upload."""
from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ValidationError

# Top-level imports so tests can patch app.workers.ingest_worker.<name>
from app.core.security import decrypt_bytes, encrypt_bytes
from app.db.repos import AuditRepo, CandidateRepo
from app.db.session import AsyncSessionLocal
from app.services import embeddings, llm, resume_parser, storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: int | None = None


class ParsedResume(BaseModel):
    skills: list[str]
    experience_years: int | None
    education: list[EducationEntry]
    languages: list[str]
    location: str | None
    seniority_level: Literal["junior", "mid", "senior", "lead", "executive"] | None
    job_titles: list[str]
    certifications: list[str]
    phone: str | None
    address: str | None


def _load_prompt() -> str:
    ref = importlib.resources.files("app.services.prompts").joinpath("parse_resume.txt")
    return ref.read_text(encoding="utf-8")


async def _call_groq(resume_text: str, strict: bool = False) -> str:
    prompt = _load_prompt().replace("{resume_text}", resume_text)
    if strict:
        prompt = "Respond with valid JSON only, no prose.\n\n" + prompt

    response = await llm.chat_completion(
        model="groq/llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        tenant_id="system",
        run_id="ingest",
        max_tokens=1024 if strict else 2048,
        temperature=0.0,
    )
    return response.choices[0].message.content


async def _parse_with_groq(resume_text: str) -> dict:
    raw = await _call_groq(resume_text, strict=False)
    try:
        return ParsedResume.model_validate_json(raw).model_dump()
    except (ValidationError, ValueError):
        logger.warning("parse_resume first attempt failed, retrying strict")

    raw2 = await _call_groq(resume_text, strict=True)
    try:
        return ParsedResume.model_validate_json(raw2).model_dump()
    except (ValidationError, ValueError):
        logger.error("parse_resume failed after retry, persisting parse_error")
        return {"parse_error": True}


async def _run_process_resume(run_id: str, candidate_id: str, tenant_id: str) -> None:
    cand_uuid = uuid.UUID(candidate_id)
    tenant_uuid = uuid.UUID(tenant_id)

    async with AsyncSessionLocal() as db:
        async with db.begin():
            repo = CandidateRepo(db)
            candidate = await repo.get(tenant_uuid, cand_uuid)
            if candidate is None:
                logger.error("process_resume: candidate %s not found", candidate_id)
                return

            if candidate.resume_text:
                logger.info("process_resume: %s already parsed, skipping", candidate_id)
                return

            file_bytes = await storage.download_resume(candidate.r2_key)
            filename = candidate.r2_key.rsplit("/", 1)[-1]
            resume_text = await resume_parser.extract_text(file_bytes, filename)

            structured = await _parse_with_groq(resume_text)

            contact = json.loads(decrypt_bytes(candidate.contact_jsonb_encrypted))
            if not structured.get("parse_error"):
                contact["phone"] = structured.pop("phone", None)
                contact["address"] = structured.pop("address", None)
            new_contact_encrypted = encrypt_bytes(json.dumps(contact).encode())

            embedding = await embeddings.embed_text(resume_text)

            await repo.update_after_parse(
                tenant_uuid,
                cand_uuid,
                resume_text=resume_text,
                structured_jsonb=structured,
                embedding=embedding,
                contact_jsonb_encrypted=new_contact_encrypted,
            )

            await AuditRepo(db).log(
                tenant_uuid,
                "candidate_parsed",
                entity_type="candidate",
                entity_id=cand_uuid,
            )


async def _run_delete_r2_object(r2_key: str) -> None:
    await storage.delete_resume(r2_key)
    logger.info("r2_deleted key=%s", r2_key)


@celery_app.task(name="ingest.process_resume", bind=True)
def process_resume(self: object, run_id: str, candidate_id: str, tenant_id: str) -> None:
    asyncio.run(_run_process_resume(run_id, candidate_id, tenant_id))


@celery_app.task(name="ingest.delete_r2_object", bind=True)
def delete_r2_object(self: object, r2_key: str) -> None:
    asyncio.run(_run_delete_r2_object(r2_key))
```

- [ ] **Step 4: Run contract tests**

```bash
pytest tests/contract/test_ingest_worker.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/workers/ingest_worker.py tests/contract/test_ingest_worker.py
git commit -m "feat: implement ingest_worker.process_resume and delete_r2_object"
```

---

## Task 11: Adversarial tests

**Files:**
- Create: `tests/adversarial/test_candidate_upload.py`

- [ ] **Step 1: Write all adversarial tests**

```python
# tests/adversarial/test_candidate_upload.py
"""Adversarial: cross-tenant, oversized file, bad extension, bad timestamp."""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest

from app.api.deps import AuthContext, get_current_user
from app.main import app
from tests.conftest import _auth_override


_BASE_FORM = {
    "name": "Adversary",
    "email": "adversary@attack.com",
    "consent_given_at": "2026-04-24T10:00:00Z",
    "consent_version": "v1",
    "consent_ip": "9.9.9.9",
}


def _file(content: bytes, name: str = "resume.pdf", mime: str = "application/pdf") -> dict:
    return {"file": (name, io.BytesIO(content), mime)}


@pytest.mark.asyncio
async def test_file_too_large_returns_413(client):
    big = b"A" * (5 * 1024 * 1024 + 1)
    resp = await client.post("/candidates", data=_BASE_FORM, files=_file(big))
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_invalid_extension_returns_422(client):
    resp = await client.post(
        "/candidates",
        data=_BASE_FORM,
        files=_file(b"MZ payload", name="evil.exe", mime="application/octet-stream"),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_consent_ip_returns_422(client):
    form = {k: v for k, v in _BASE_FORM.items() if k != "consent_ip"}
    resp = await client.post("/candidates", data=form, files=_file(b"%PDF stub"))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_timestamp_returns_422(client):
    form = {**_BASE_FORM, "consent_given_at": "not-a-date"}
    resp = await client.post("/candidates", data=form, files=_file(b"%PDF stub"))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_404(client, unique_form):
    """Upload under test tenant; GET as a different tenant must return 404."""
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        post = await client.post("/candidates", data=unique_form, files=_file(b"%PDF stub"))
    cid = post.json()["id"]

    other_tenant = uuid.UUID("00000000-0000-0000-0000-000000000099")

    def _other_auth() -> AuthContext:
        return AuthContext(
            tenant_id=other_tenant,
            user_id=uuid.uuid4(),
            clerk_user_id="attacker",
        )

    app.dependency_overrides[get_current_user] = _other_auth
    try:
        resp = await client.get(f"/candidates/{cid}")
    finally:
        app.dependency_overrides[get_current_user] = _auth_override

    assert resp.status_code == 404
```

- [ ] **Step 2: Run adversarial tests**

```bash
pytest tests/adversarial/test_candidate_upload.py -v
```

Expected: 5 tests PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/adversarial/test_candidate_upload.py
git commit -m "test: adversarial tests for candidate upload"
```

---

## Task 12: Full suite + coverage check

- [ ] **Step 1: Run all tests**

```bash
pytest tests/unit/ tests/contract/ tests/integration/ tests/adversarial/ -v --tb=short
```

Expected: all tests PASSED.

- [ ] **Step 2: Check coverage on critical paths**

```bash
pytest tests/ \
  --cov=app/workers/ingest_worker \
  --cov=app/api/candidates \
  --cov-report=term-missing \
  -q
```

Expected: ≥100% on `ingest_worker.py` (all branches) and `candidates.py` dedup path.

- [ ] **Step 3: Smoke test the running API**

```bash
make dev
# In a second terminal:
curl -s -X POST http://localhost:8000/candidates \
  -F "name=Jane Doe" \
  -F "email=jane@smoke.com" \
  -F "consent_given_at=2026-04-24T10:00:00Z" \
  -F "consent_version=v1" \
  -F "consent_ip=127.0.0.1" \
  -F "file=@tests/fixtures/sample.pdf"
```

Expected: `{"id": "...", "status": "processing", "created_at": "..."}` with HTTP 202.

- [ ] **Step 4: Write Phase 2 ADR**

Create `docs/adr/0003-phase-2-complete.md` documenting:
- What you built
- Trade-offs (approach A: explicit name/email over fully-async extraction)
- What was stubbed (GDPR orphan blob cleanup job — Phase 8)
- What's next (Phase 3: retrieval + rerank)

- [ ] **Step 5: Final commit**

```bash
git add docs/adr/0003-phase-2-complete.md
git commit -m "docs: Phase 2 complete ADR"
```
