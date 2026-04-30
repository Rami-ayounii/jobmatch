"""Contract tests for the rerank_candidates pipeline node."""
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

_BASE_STATE: PipelineState = {
    "tenant_id": TENANT_ID,
    "job_id": "00000000-0000-0000-0000-000000000099",
    "job_description": "Python developer needed",
    "job_embedding": [0.1] * 1536,
    "retrieved_candidates": _FAKE_CANDIDATES,
}


@pytest.mark.asyncio
async def test_rerank_returns_top_15() -> None:
    scores = [0.5] * 50
    scores[6] = 0.99  # candidate 7 gets top score

    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=scores)):
        result = await rerank_candidates(_BASE_STATE)

    ranked = result["reranked_candidates"]
    assert len(ranked) == 15
    assert ranked[0]["candidate_id"] == "00000000-0000-0000-0000-000000000007"
    assert ranked[0]["rerank_score"] == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_rerank_fewer_than_15() -> None:
    three = _FAKE_CANDIDATES[:3]
    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=[0.9, 0.8, 0.7])):
        result = await rerank_candidates({**_BASE_STATE, "retrieved_candidates": three})

    assert len(result["reranked_candidates"]) == 3


@pytest.mark.asyncio
async def test_rerank_empty_candidates() -> None:
    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=[])):
        result = await rerank_candidates({**_BASE_STATE, "retrieved_candidates": []})

    assert result["reranked_candidates"] == []


@pytest.mark.asyncio
async def test_rerank_preserves_state_keys() -> None:
    with patch("app.pipeline.nodes.rerank.reranker.rerank", AsyncMock(return_value=[0.5] * 50)):
        result = await rerank_candidates({**_BASE_STATE, "job_title": "Python Engineer"})

    assert result["job_title"] == "Python Engineer"


@pytest.mark.asyncio
async def test_rerank_passes_job_description_as_query() -> None:
    mock_rerank = AsyncMock(return_value=[0.5] * 50)
    with patch("app.pipeline.nodes.rerank.reranker.rerank", mock_rerank):
        await rerank_candidates({**_BASE_STATE, "job_description": "Senior Python engineers needed"})

    assert mock_rerank.call_args.args[0] == "Senior Python engineers needed"
    assert len(mock_rerank.call_args.args[1]) == 50
