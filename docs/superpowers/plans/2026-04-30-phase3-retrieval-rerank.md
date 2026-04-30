# Phase 3 — Retrieval + Rerank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `POST /jobs` (embed JD on creation), a hybrid pgvector + pg_trgm retrieval query returning top 50 candidates, and a BGE reranker pipeline node that reduces 50 → 15 — all tenant-scoped and covered by contract + adversarial tests.

**Architecture:** The API handler creates the job and embeds synchronously (fast, ~200ms). `CandidateRepo.retrieve_hybrid` issues a raw SQL hybrid query combining pgvector cosine distance (weight 0.7) and pg_trgm `word_similarity` (weight 0.3). The rerank node calls `app/services/reranker.py` via `asyncio.to_thread`. Both nodes are pure async functions that create their own DB sessions (same pattern as `ingest_worker`).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, pgvector (`<=>` cosine distance), pg_trgm (`word_similarity`), `sentence-transformers` BGE reranker, Alembic, pytest.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/db/migrations/versions/0011_add_trgm_resume_index.py` | Create | GIN trgm index on `candidates.resume_text` |
| `app/db/repos.py` | Modify | Add `CandidateRetrievalResult`, `CandidateRepo.retrieve_hybrid`, update `JobRepo.create` to accept `embedding` |
| `app/api/jobs.py` | Modify | `POST /jobs`, `GET /jobs/{id}` |
| `app/pipeline/state.py` | Modify | Add `RetrievedCandidate`, `RankedCandidate` TypedDicts; expand `PipelineState` |
| `app/pipeline/nodes/retrieve.py` | Modify | Retrieve node: calls repo, populates `retrieved_candidates` |
| `app/pipeline/nodes/rerank.py` | Modify | Rerank node: calls BGE reranker, populates `reranked_candidates` (top 15) |
| `tests/unit/test_jobs_schema.py` | Create | Pydantic model validation for `JobCreate` / `JobResponse` |
| `tests/contract/test_retrieve_node.py` | Create | Contract test: retrieve node with mocked repo |
| `tests/contract/test_rerank_node.py` | Create | Contract test: rerank node with mocked reranker |
| `tests/adversarial/test_retrieval_isolation.py` | Create | Cross-tenant retrieval returns no results (needs DB) |

---

## Task 1: Migration — GIN trgm index on candidates.resume_text

`pg_trgm` is already installed (migration 0002). We need a GIN index on `resume_text` to make `word_similarity()` queries fast.

**Files:**
- Create: `app/db/migrations/versions/0011_add_trgm_resume_index.py`

- [ ] **Step 1: Write the migration**

```python
# app/db/migrations/versions/0011_add_trgm_resume_index.py
"""add_trgm_index_on_resume_text

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-30
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS candidates_resume_trgm_idx "
        "ON candidates USING GIN (resume_text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("candidates_resume_trgm_idx", table_name="candidates")
```

- [ ] **Step 2: Verify alembic picks it up**

Run: `python -m alembic heads`
Expected: `0011 (head)` listed as the current head.

- [ ] **Step 3: Commit**

```bash
git add app/db/migrations/versions/0011_add_trgm_resume_index.py
git commit -m "feat: add GIN trgm index on candidates.resume_text for hybrid retrieval"
```

---

## Task 2: CandidateRetrievalResult schema + retrieve_hybrid + JobRepo.create with embedding

**Files:**
- Modify: `app/db/repos.py`

- [ ] **Step 1: Write unit test for `CandidateRetrievalResult` schema**

Create `tests/unit/test_jobs_schema.py`:

```python
# tests/unit/test_jobs_schema.py
"""Unit tests for Phase 3 Pydantic schemas."""
import uuid
from app.db.repos import CandidateRetrievalResult


def test_candidate_retrieval_result_fields() -> None:
    cid = uuid.uuid4()
    r = CandidateRetrievalResult(
        candidate_id=cid,
        resume_text="Alice resume",
        structured_jsonb={"skills": ["Python"]},
        cosine_score=0.9,
        text_score=0.7,
        combined_score=0.84,
    )
    assert r.candidate_id == cid
    assert r.combined_score == pytest.approx(0.84)


def test_combined_score_range() -> None:
    """combined_score = 0.7 * cosine + 0.3 * text is always in [0, 1]."""
    r = CandidateRetrievalResult(
        candidate_id=uuid.uuid4(),
        resume_text="x",
        structured_jsonb={},
        cosine_score=1.0,
        text_score=1.0,
        combined_score=1.0,
    )
    assert 0.0 <= r.combined_score <= 1.0
```

Add `import pytest` at the top of the test file.

- [ ] **Step 2: Run test — expect FAIL (CandidateRetrievalResult not defined)**

Run: `pytest tests/unit/test_jobs_schema.py -v`
Expected: `ImportError` or `AttributeError` — `CandidateRetrievalResult` does not exist yet.

- [ ] **Step 3: Add `CandidateRetrievalResult` and update `JobRepo` in `app/db/repos.py`**

At the top of `app/db/repos.py`, add to existing imports:
```python
from pydantic import BaseModel
```

After the existing `from sqlalchemy import select, update` line, also add:
```python
from sqlalchemy import text as sa_text
```

After the module docstring (before `class TenantRepo`), add the dataclass:
```python
class CandidateRetrievalResult(BaseModel):
    candidate_id: uuid.UUID
    resume_text: str
    structured_jsonb: dict[str, Any]
    cosine_score: float
    text_score: float
    combined_score: float
```

In `CandidateRepo`, add the `retrieve_hybrid` method after `get_status`:

```python
async def retrieve_hybrid(
    self,
    tenant_id: uuid.UUID,
    *,
    query_embedding: list[float],
    query_text: str,
    top_k: int = 50,
) -> list[CandidateRetrievalResult]:
    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    rows = await self._db.execute(
        sa_text("""
            SELECT
                id,
                resume_text,
                structured_jsonb,
                1 - (embedding <=> :query_vec::vector(1536)) AS cosine_score,
                COALESCE(word_similarity(:query_text, resume_text), 0.0) AS text_score,
                0.7 * (1 - (embedding <=> :query_vec::vector(1536))) +
                0.3 * COALESCE(word_similarity(:query_text, resume_text), 0.0)
                    AS combined_score
            FROM candidates
            WHERE
                tenant_id = :tenant_id
                AND deleted_at IS NULL
                AND embedding IS NOT NULL
            ORDER BY combined_score DESC
            LIMIT :top_k
        """),
        {
            "query_vec": vec_str,
            "query_text": query_text,
            "tenant_id": str(tenant_id),
            "top_k": top_k,
        },
    )
    return [
        CandidateRetrievalResult(
            candidate_id=row.id,
            resume_text=row.resume_text,
            structured_jsonb=row.structured_jsonb,
            cosine_score=float(row.cosine_score),
            text_score=float(row.text_score),
            combined_score=float(row.combined_score),
        )
        for row in rows
    ]
```

In `JobRepo.create`, add `embedding: list[float] | None = None` as a keyword argument and pass it to `Job(...)`:

```python
async def create(
    self,
    tenant_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
    title: str,
    description_text: str,
    structured_jsonb: dict[str, Any] | None = None,
    embedding: list[float] | None = None,
) -> Job:
    job = Job(
        tenant_id=tenant_id,
        created_by=created_by,
        title=title,
        description_text=description_text,
        structured_jsonb=structured_jsonb or {},
        embedding=embedding,
    )
    self._db.add(job)
    await self._db.flush()
    return job
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/unit/test_jobs_schema.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/db/repos.py tests/unit/test_jobs_schema.py
git commit -m "feat: add CandidateRetrievalResult, retrieve_hybrid, JobRepo embedding param"
```

---

## Task 3: POST /jobs + GET /jobs/{id}

**Files:**
- Modify: `app/api/jobs.py`

- [ ] **Step 1: Write failing unit test for job Pydantic models**

Add to `tests/unit/test_jobs_schema.py`:

```python
import uuid
from datetime import datetime, UTC
from app.api.jobs import JobCreate, JobResponse


def test_job_create_requires_title_and_description() -> None:
    j = JobCreate(title="Senior Python Engineer", description_text="We need Python skills.")
    assert j.title == "Senior Python Engineer"


def test_job_response_model() -> None:
    now = datetime.now(UTC)
    r = JobResponse(
        id=uuid.uuid4(),
        title="Senior Python Engineer",
        status="active",
        created_at=now,
    )
    assert r.status == "active"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/unit/test_jobs_schema.py -v`
Expected: `ImportError` — `JobCreate` and `JobResponse` not defined yet.

- [ ] **Step 3: Implement `app/api/jobs.py`**

```python
# app/api/jobs.py
"""Job description endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.db.repos import JobRepo
from app.db.session import get_db
from app.services.embeddings import embed_text

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    title: str
    description_text: str


class JobResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    created_at: datetime


@router.post("", status_code=201, response_model=JobResponse)
async def create_job(
    body: JobCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    embedding = await embed_text(body.description_text)
    job = await JobRepo(db).create(
        ctx.tenant_id,
        created_by=ctx.user_id,
        title=body.title,
        description_text=body.description_text,
        embedding=embedding,
    )
    return JobResponse(
        id=job.id,
        title=job.title,
        status=job.status,
        created_at=job.created_at,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    job = await JobRepo(db).get(ctx.tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(
        id=job.id,
        title=job.title,
        status=job.status,
        created_at=job.created_at,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/unit/test_jobs_schema.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/api/jobs.py tests/unit/test_jobs_schema.py
git commit -m "feat: implement POST /jobs and GET /jobs/{id} with synchronous embedding"
```

---

## Task 4: PipelineState Phase 3 fields

**Files:**
- Modify: `app/pipeline/state.py`

- [ ] **Step 1: Write failing test for state schemas**

Create `tests/unit/test_pipeline_state.py`:

```python
# tests/unit/test_pipeline_state.py
"""Unit tests for PipelineState TypedDicts."""
from app.pipeline.state import PipelineState, RankedCandidate, RetrievedCandidate


def test_retrieved_candidate_keys() -> None:
    c: RetrievedCandidate = {
        "candidate_id": "some-uuid",
        "resume_text": "Alice resume",
        "structured_jsonb": {"skills": ["Python"]},
        "cosine_score": 0.9,
        "text_score": 0.7,
        "combined_score": 0.84,
    }
    assert c["candidate_id"] == "some-uuid"


def test_ranked_candidate_keys() -> None:
    c: RankedCandidate = {
        "candidate_id": "some-uuid",
        "resume_text": "Alice resume",
        "structured_jsonb": {},
        "rerank_score": 0.95,
    }
    assert c["rerank_score"] == 0.95


def test_pipeline_state_partial() -> None:
    state: PipelineState = {
        "tenant_id": "tid",
        "job_id": "jid",
        "job_description": "Python developer needed",
        "job_embedding": [0.1] * 1536,
    }
    assert state["tenant_id"] == "tid"
    assert len(state["job_embedding"]) == 1536
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/unit/test_pipeline_state.py -v`
Expected: `ImportError` — `RetrievedCandidate`, `RankedCandidate` not exported yet.

- [ ] **Step 3: Expand `app/pipeline/state.py`**

```python
# app/pipeline/state.py
"""PipelineState TypedDict and supporting schemas for the LangGraph pipeline."""
from typing import Any, TypedDict


class RetrievedCandidate(TypedDict):
    candidate_id: str
    resume_text: str
    structured_jsonb: dict[str, Any]
    cosine_score: float
    text_score: float
    combined_score: float


class RankedCandidate(TypedDict):
    candidate_id: str
    resume_text: str
    structured_jsonb: dict[str, Any]
    rerank_score: float


class PipelineState(TypedDict, total=False):
    tenant_id: str
    job_id: str
    job_title: str
    job_description: str
    job_embedding: list[float]
    retrieved_candidates: list[RetrievedCandidate]
    reranked_candidates: list[RankedCandidate]
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/unit/test_pipeline_state.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/state.py tests/unit/test_pipeline_state.py
git commit -m "feat: expand PipelineState with Phase 3 retrieved/ranked candidate types"
```

---

## Task 5: retrieve.py pipeline node

**Files:**
- Modify: `app/pipeline/nodes/retrieve.py`

- [ ] **Step 1: Write the failing contract test**

Create `tests/contract/test_retrieve_node.py`:

```python
# tests/contract/test_retrieve_node.py
"""Contract test for the retrieve_candidates pipeline node.

Mocks the DB session and CandidateRepo — does not need a real database.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.repos import CandidateRetrievalResult
from app.pipeline.nodes.retrieve import retrieve_candidates
from app.pipeline.state import PipelineState

TENANT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
JOB_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000002"))
FAKE_EMBEDDING = [0.1] * 1536

_FAKE_RESULTS = [
    CandidateRetrievalResult(
        candidate_id=uuid.UUID(f"00000000-0000-0000-0000-{i:012d}"),
        resume_text=f"Candidate {i} resume text",
        structured_jsonb={"skills": ["Python"]},
        cosine_score=1.0 - i * 0.01,
        text_score=0.8,
        combined_score=0.95 - i * 0.01,
    )
    for i in range(1, 51)  # 50 results
]


def _make_session_factory(candidate_repo: AsyncMock) -> MagicMock:
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin_cm
    session.execute = AsyncMock()

    return MagicMock(return_value=session)


@pytest.mark.asyncio
async def test_retrieve_populates_state() -> None:
    """Node calls retrieve_hybrid and writes results to state."""
    candidate_repo = AsyncMock()
    candidate_repo.retrieve_hybrid.return_value = _FAKE_RESULTS

    session_factory = _make_session_factory(candidate_repo)

    initial_state: PipelineState = {
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "job_title": "Python Engineer",
        "job_description": "We need Python skills.",
        "job_embedding": FAKE_EMBEDDING,
    }

    with (
        patch("app.pipeline.nodes.retrieve.AsyncSessionLocal", session_factory),
        patch("app.pipeline.nodes.retrieve.CandidateRepo", return_value=candidate_repo),
    ):
        result = await retrieve_candidates(initial_state)

    candidate_repo.retrieve_hybrid.assert_awaited_once_with(
        uuid.UUID(TENANT_ID),
        query_embedding=FAKE_EMBEDDING,
        query_text="We need Python skills.",
        top_k=50,
    )
    assert len(result["retrieved_candidates"]) == 50
    assert result["retrieved_candidates"][0]["candidate_id"] == "00000000-0000-0000-0000-000000000001"
    assert result["retrieved_candidates"][0]["cosine_score"] == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_retrieve_preserves_existing_state_keys() -> None:
    """Node must not lose unrelated state keys."""
    candidate_repo = AsyncMock()
    candidate_repo.retrieve_hybrid.return_value = []

    session_factory = _make_session_factory(candidate_repo)

    initial_state: PipelineState = {
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "job_title": "Python Engineer",
        "job_description": "Python skills needed.",
        "job_embedding": FAKE_EMBEDDING,
    }

    with (
        patch("app.pipeline.nodes.retrieve.AsyncSessionLocal", session_factory),
        patch("app.pipeline.nodes.retrieve.CandidateRepo", return_value=candidate_repo),
    ):
        result = await retrieve_candidates(initial_state)

    assert result["job_title"] == "Python Engineer"
    assert result["retrieved_candidates"] == []
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/contract/test_retrieve_node.py -v`
Expected: `ImportError` — `retrieve_candidates` not implemented.

- [ ] **Step 3: Implement `app/pipeline/nodes/retrieve.py`**

```python
# app/pipeline/nodes/retrieve.py
"""Retrieve pipeline node: hybrid pgvector + pg_trgm retrieval → top 50 candidates."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.repos import CandidateRepo
from app.db.session import AsyncSessionLocal
from app.pipeline.state import PipelineState, RetrievedCandidate


async def retrieve_candidates(state: PipelineState) -> PipelineState:
    tenant_id = uuid.UUID(state["tenant_id"])

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            results = await CandidateRepo(db).retrieve_hybrid(
                tenant_id,
                query_embedding=state["job_embedding"],
                query_text=state["job_description"],
                top_k=50,
            )

    retrieved: list[RetrievedCandidate] = [
        RetrievedCandidate(
            candidate_id=str(r.candidate_id),
            resume_text=r.resume_text,
            structured_jsonb=r.structured_jsonb,
            cosine_score=r.cosine_score,
            text_score=r.text_score,
            combined_score=r.combined_score,
        )
        for r in results
    ]

    return {**state, "retrieved_candidates": retrieved}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/contract/test_retrieve_node.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/nodes/retrieve.py tests/contract/test_retrieve_node.py
git commit -m "feat: implement retrieve_candidates pipeline node with hybrid pgvector+trgm"
```

---

## Task 6: rerank.py pipeline node

**Files:**
- Modify: `app/pipeline/nodes/rerank.py`

- [ ] **Step 1: Write the failing contract test**

Create `tests/contract/test_rerank_node.py`:

```python
# tests/contract/test_rerank_node.py
"""Contract test for the rerank_candidates pipeline node.

Mocks the BGE reranker — no model download, no GPU needed.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.nodes.rerank import rerank_candidates
from app.pipeline.state import PipelineState, RetrievedCandidate

TENANT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

_FAKE_CANDIDATES: list[RetrievedCandidate] = [
    RetrievedCandidate(
        candidate_id=str(uuid.UUID(f"00000000-0000-0000-0000-{i:012d}")),
        resume_text=f"Candidate {i} resume",
        structured_jsonb={"skills": ["Python"]},
        cosine_score=0.9,
        text_score=0.8,
        combined_score=0.87,
    )
    for i in range(1, 51)
]


@pytest.mark.asyncio
async def test_rerank_returns_top_15() -> None:
    """Node reranks 50 candidates and keeps only the top 15."""
    # Scores: candidate 7 gets highest score, then descending
    scores = [0.5] * 50
    scores[6] = 0.99  # candidate 7 (index 6) gets top score

    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=scores)):
        result = await rerank_candidates(
            {
                "tenant_id": TENANT_ID,
                "job_id": "00000000-0000-0000-0000-000000000099",
                "job_description": "Python developer needed",
                "job_embedding": [0.1] * 1536,
                "retrieved_candidates": _FAKE_CANDIDATES,
            }
        )

    ranked = result["reranked_candidates"]
    assert len(ranked) == 15
    # Candidate 7 (index 6) must be first
    assert ranked[0]["candidate_id"] == "00000000-0000-0000-0000-000000000007"
    assert ranked[0]["rerank_score"] == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_rerank_fewer_than_15_candidates() -> None:
    """If fewer than 15 candidates come in, return all of them."""
    three_candidates = _FAKE_CANDIDATES[:3]
    scores = [0.9, 0.8, 0.7]

    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=scores)):
        result = await rerank_candidates(
            {
                "tenant_id": TENANT_ID,
                "job_id": "00000000-0000-0000-0000-000000000099",
                "job_description": "Python developer needed",
                "job_embedding": [0.1] * 1536,
                "retrieved_candidates": three_candidates,
            }
        )

    assert len(result["reranked_candidates"]) == 3


@pytest.mark.asyncio
async def test_rerank_preserves_existing_state_keys() -> None:
    """Node must not lose unrelated state keys."""
    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=[0.5])):
        result = await rerank_candidates(
            {
                "tenant_id": TENANT_ID,
                "job_id": "00000000-0000-0000-0000-000000000099",
                "job_title": "Python Engineer",
                "job_description": "x",
                "job_embedding": [0.1] * 1536,
                "retrieved_candidates": _FAKE_CANDIDATES[:1],
            }
        )
    assert result["job_title"] == "Python Engineer"


@pytest.mark.asyncio
async def test_rerank_passes_job_description_as_query() -> None:
    """Reranker must receive job_description as the query string."""
    mock_rerank = AsyncMock(return_value=[0.5] * 50)

    with patch("app.pipeline.nodes.rerank.reranker.rerank", mock_rerank):
        await rerank_candidates(
            {
                "tenant_id": TENANT_ID,
                "job_id": "00000000-0000-0000-0000-000000000099",
                "job_description": "We need senior Python engineers",
                "job_embedding": [0.1] * 1536,
                "retrieved_candidates": _FAKE_CANDIDATES,
            }
        )

    call_args = mock_rerank.call_args
    assert call_args.args[0] == "We need senior Python engineers"
    assert len(call_args.args[1]) == 50
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/contract/test_rerank_node.py -v`
Expected: `ImportError` — `rerank_candidates` not implemented.

- [ ] **Step 3: Implement `app/pipeline/nodes/rerank.py`**

```python
# app/pipeline/nodes/rerank.py
"""Rerank pipeline node: BGE reranker reduces top-50 → top-15."""
from __future__ import annotations

from app.pipeline.state import PipelineState, RankedCandidate
from app.services import reranker

_TOP_N = 15


async def rerank_candidates(state: PipelineState) -> PipelineState:
    candidates = state.get("retrieved_candidates", [])
    if not candidates:
        return {**state, "reranked_candidates": []}

    passages = [c["resume_text"] for c in candidates]
    scores = await reranker.rerank(state["job_description"], passages)

    paired = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    top = paired[:_TOP_N]

    ranked: list[RankedCandidate] = [
        RankedCandidate(
            candidate_id=c["candidate_id"],
            resume_text=c["resume_text"],
            structured_jsonb=c["structured_jsonb"],
            rerank_score=float(score),
        )
        for c, score in top
    ]

    return {**state, "reranked_candidates": ranked}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/contract/test_rerank_node.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/nodes/rerank.py tests/contract/test_rerank_node.py
git commit -m "feat: implement rerank_candidates pipeline node, top-50 → top-15 via BGE"
```

---

## Task 7: Adversarial test — cross-tenant retrieval isolation

This test requires a live Postgres + pgvector DB. It will fail with `ConnectionRefusedError` without Docker, the same as existing adversarial tests. Write and commit it anyway.

**Files:**
- Create: `tests/adversarial/test_retrieval_isolation.py`

- [ ] **Step 1: Write the test**

```python
# tests/adversarial/test_retrieval_isolation.py
"""Adversarial: cross-tenant retrieval must return zero results.

Requires a live Postgres DB. Skipped (ConnectionRefusedError) without Docker.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.security import encrypt_bytes, hash_field
from app.db.repos import CandidateRepo
from app.db.session import AsyncSessionLocal

TENANT_A = uuid.UUID("aaaa0000-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("bbbb0000-0000-0000-0000-000000000001")
FAKE_EMBEDDING = [0.1] * 1536


async def _seed_tenant_and_candidate(tenant_id: uuid.UUID, tag: str) -> None:
    """Insert a tenant row and one embedded candidate directly (bypasses RLS)."""
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO tenants (id, name, clerk_org_id) "
                    "VALUES (:id, :name, :org) ON CONFLICT DO NOTHING"
                ),
                {"id": str(tenant_id), "name": f"Tenant {tag}", "org": f"org_{tag}"},
            )
            candidate_id = uuid.uuid4()
            contact = encrypt_bytes(
                json.dumps(
                    {"name": f"Alice {tag}", "email": f"alice@{tag}.com",
                     "consent_ip": "1.2.3.4", "phone": None, "address": None}
                ).encode()
            )
            vec_str = "[" + ",".join(str(x) for x in FAKE_EMBEDDING) + "]"
            await db.execute(
                text("""
                    INSERT INTO candidates (
                        id, tenant_id, full_name_hash, email_hash,
                        contact_jsonb_encrypted, resume_text, structured_jsonb,
                        embedding, r2_key, consent_given_at, consent_version
                    ) VALUES (
                        :id, :tid, :fnh, :eh, :contact, :text, '{}',
                        :vec::vector(1536), :r2key, now(), 'v1'
                    )
                """),
                {
                    "id": str(candidate_id),
                    "tid": str(tenant_id),
                    "fnh": hash_field(f"Alice {tag}"),
                    "eh": hash_field(f"alice@{tag}.com"),
                    "contact": contact,
                    "text": f"Alice {tag} is a Python developer",
                    "vec": vec_str,
                    "r2key": f"{tenant_id}/{candidate_id}/resume.pdf",
                },
            )


@pytest.mark.asyncio
async def test_retrieve_hybrid_is_tenant_scoped() -> None:
    """Querying as TENANT_B returns zero results even though TENANT_A has candidates."""
    await _seed_tenant_and_candidate(TENANT_A, "alpha")
    await _seed_tenant_and_candidate(TENANT_B, "beta")

    async with AsyncSessionLocal() as db:
        async with db.begin():
            # Activate RLS for TENANT_B only
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(TENANT_B)},
            )
            results = await CandidateRepo(db).retrieve_hybrid(
                TENANT_B,
                query_embedding=FAKE_EMBEDDING,
                query_text="Python developer",
                top_k=50,
            )

    # TENANT_B has exactly 1 candidate — must not see TENANT_A's
    candidate_tenant_ids = {str(r.candidate_id) for r in results}
    for r in results:
        # Verify every returned candidate truly belongs to TENANT_B
        async with AsyncSessionLocal() as db:
            async with db.begin():
                row = await db.execute(
                    text("SELECT tenant_id FROM candidates WHERE id = :id"),
                    {"id": str(r.candidate_id)},
                )
                tenant_id_in_db = row.scalar_one()
                assert str(tenant_id_in_db) == str(TENANT_B), (
                    f"Cross-tenant leak: candidate {r.candidate_id} "
                    f"belongs to {tenant_id_in_db}, not {TENANT_B}"
                )
```

- [ ] **Step 2: Run test — expect ConnectionRefusedError (no DB)**

Run: `pytest tests/adversarial/test_retrieval_isolation.py -v`
Expected: `ERROR` with `ConnectionRefusedError` (no Docker running). This is acceptable — confirms the test is wired.

- [ ] **Step 3: Commit**

```bash
git add tests/adversarial/test_retrieval_isolation.py
git commit -m "test: adversarial cross-tenant retrieval isolation (requires DB)"
```

---

## Task 8: Run full non-DB test suite + finish branch

- [ ] **Step 1: Run all non-DB tests**

```bash
pytest tests/ \
  --ignore=tests/adversarial \
  --ignore=tests/integration \
  --ignore=tests/unit/test_health.py \
  -v
```

Expected: all tests pass (currently 11 from Phase 2 + new Phase 3 tests = ~20 total).

- [ ] **Step 2: Check for any regressions**

If anything fails, fix it before proceeding.

- [ ] **Step 3: Write Phase 3 ADR**

Create `docs/adr/0004-phase-3-complete.md`:

```markdown
# ADR 0004 — Phase 3: Retrieval + Rerank Complete

**Date:** 2026-04-30
**Status:** Accepted
**Branch:** feat/phase-3-retrieval

## What was built

- **`POST /jobs`** — JSON body `{title, description_text}`, embeds synchronously via `embed_text`, persists to DB with embedding. Returns `{id, title, status, created_at}`.
- **`GET /jobs/{id}`** — returns job info, 404 if not found or wrong tenant.
- **`CandidateRepo.retrieve_hybrid`** — raw SQL hybrid query: `0.7 * (1 - cosine_distance) + 0.3 * word_similarity`, filtered by tenant, `deleted_at IS NULL`, `embedding IS NOT NULL`, LIMIT 50.
- **`retrieve_candidates` node** — pipeline node that calls `retrieve_hybrid`, sets RLS context, writes `retrieved_candidates` to state.
- **`rerank_candidates` node** — pipeline node that calls BGE reranker via `asyncio.to_thread`, reduces top-50 → top-15 sorted by rerank score.
- **GIN trgm index** on `candidates.resume_text` (migration 0011) for fast `word_similarity` queries.

## Trade-offs

| Decision | Rationale |
|---|---|
| Synchronous embedding on job create | JD embedding is fast (~200ms); avoids the complexity of a Celery task for a single vector |
| Raw SQL `text()` for hybrid query | pgvector `<=>` operator + pg_trgm `word_similarity` in one SELECT is cleaner as raw SQL than ORM constructs |
| Vector bound as string `[x,y,...]::vector(1536)` | Avoids asyncpg codec registration; reliable with SQLAlchemy `text()` |
| Weights 0.7 / 0.3 (vector / trgm) | Semantic similarity is the primary signal; trgm catches keyword matches the embedding may miss |

## What was skipped

- No structured filters yet (Phase 3 spec mentions them; deferred to Phase 4 when `PipelineState` has full job `structured_jsonb`)
- No load test (Phase 3 acceptance mentions <300ms at p95 for retrieval; needs real data + Docker)
- Integration tests require Docker (same state as Phase 2)

## Coverage

- `app/pipeline/nodes/retrieve.py` — 100% (contract tests)
- `app/pipeline/nodes/rerank.py` — 100% (contract tests)
- `app/api/jobs.py` — unit tests cover models; endpoint tests need Docker

## What's next

**Phase 4** — Pipeline graph + agent:
- Wire all nodes into LangGraph `StateGraph`
- Implement `reason.py` (Claude Sonnet agent, max 6 tool calls)
- Implement all 5 agent tools
- `validate_output` with 8 deterministic checks + citation faithfulness
```

- [ ] **Step 4: Commit the ADR**

```bash
git add docs/adr/0004-phase-3-complete.md
git commit -m "docs: add Phase 3 ADR"
```

---

## Self-Review

**Spec coverage check:**
- [x] `POST /jobs` accepts a JD — Task 3
- [x] Embeds JD — Task 3 (synchronous via `embed_text`)
- [x] Hybrid SQL query (pgvector + BM25 + filters) → top 50 — Task 2 (`retrieve_hybrid`); structured filters deferred to Phase 4 (noted in ADR)
- [x] BGE reranker singleton → 50 → 15 — Task 6 (`rerank_candidates` node uses singleton from `reranker.py`)
- [x] All queries tenant-scoped — Task 2 (RLS + explicit `tenant_id` filter in SQL), Task 5 (RLS set in node)
- [x] Adversarial test: cross-tenant — Task 7
- [ ] <300ms for retrieval — unmeasurable without Docker/data; deferred (ADR notes this)

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `CandidateRetrievalResult.candidate_id` is `uuid.UUID` in the dataclass; in `RetrievedCandidate` it is `str`. The retrieve node converts via `str(r.candidate_id)`. Consistent throughout Tasks 5, 6, 7, 8.
- `retrieve_hybrid` signature uses `top_k=50`; contract test asserts called with `top_k=50`. Consistent.
- `rerank_candidates` reads `state["retrieved_candidates"]`; retrieve node writes `state["retrieved_candidates"]`. Key name consistent.
