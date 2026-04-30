"""Contract tests for the retrieve_candidates pipeline node."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.repos import CandidateRetrievalResult
from app.pipeline.nodes.retrieve import retrieve_candidates
from app.pipeline.state import PipelineState

TENANT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
FAKE_EMBEDDING = [0.1] * 1536

_FAKE_RESULTS = [
    CandidateRetrievalResult(
        candidate_id=uuid.UUID(f"00000000-0000-0000-0000-{i:012d}"),
        resume_text=f"Candidate {i} resume",
        structured_jsonb={"skills": ["Python"]},
        cosine_score=1.0 - i * 0.01,
        text_score=0.8,
        combined_score=0.95 - i * 0.01,
    )
    for i in range(1, 51)
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
    candidate_repo = AsyncMock()
    candidate_repo.retrieve_hybrid.return_value = _FAKE_RESULTS
    session_factory = _make_session_factory(candidate_repo)

    state: PipelineState = {
        "tenant_id": TENANT_ID,
        "job_id": "00000000-0000-0000-0000-000000000099",
        "job_title": "Python Engineer",
        "job_description": "We need Python skills.",
        "job_embedding": FAKE_EMBEDDING,
    }

    with (
        patch("app.pipeline.nodes.retrieve.AsyncSessionLocal", session_factory),
        patch("app.pipeline.nodes.retrieve.CandidateRepo", return_value=candidate_repo),
    ):
        result = await retrieve_candidates(state)

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
async def test_retrieve_preserves_state_keys() -> None:
    candidate_repo = AsyncMock()
    candidate_repo.retrieve_hybrid.return_value = []
    session_factory = _make_session_factory(candidate_repo)

    state: PipelineState = {
        "tenant_id": TENANT_ID,
        "job_id": "00000000-0000-0000-0000-000000000099",
        "job_title": "Python Engineer",
        "job_description": "x",
        "job_embedding": FAKE_EMBEDDING,
    }

    with (
        patch("app.pipeline.nodes.retrieve.AsyncSessionLocal", session_factory),
        patch("app.pipeline.nodes.retrieve.CandidateRepo", return_value=candidate_repo),
    ):
        result = await retrieve_candidates(state)

    assert result["job_title"] == "Python Engineer"
    assert result["retrieved_candidates"] == []
