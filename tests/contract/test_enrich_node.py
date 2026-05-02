"""Contract tests for enrich_candidates pipeline node."""
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from app.pipeline.nodes.enrich import enrich_candidates
from app.pipeline.state import PipelineState, RankedCandidate

TENANT_ID = str(uuid.uuid4())

_FAKE_RANKED: list[RankedCandidate] = [
    {
        "candidate_id": str(uuid.uuid4()),
        "resume_text": f"Candidate {i} has 5 years of Python experience.",
        "structured_jsonb": {"skills": ["Python"], "experience_years": 5},
        "rerank_score": 0.9 - i * 0.01,
    }
    for i in range(3)
]

_ENRICHED_RESPONSE = '{"summary": "Python dev", "top_skills": ["Python"], "years_experience": 5, "highest_education": "BS Computer Science", "industries": ["tech"]}'


@pytest.mark.asyncio
async def test_enrich_produces_enriched_candidates() -> None:
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = _ENRICHED_RESPONSE
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    state: PipelineState = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "reranked_candidates": _FAKE_RANKED,
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }

    with patch("app.pipeline.nodes.enrich.llm.chat_completion", AsyncMock(return_value=mock_response)):
        result = await enrich_candidates(state)

    assert len(result["enriched_candidates"]) == 3
    first = result["enriched_candidates"][0]
    assert first["enriched_fields"]["summary"] == "Python dev"
    assert first["rerank_score"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_enrich_handles_parse_failure_gracefully() -> None:
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = "invalid json {"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5

    state: PipelineState = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "reranked_candidates": _FAKE_RANKED[:1],
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }

    with patch("app.pipeline.nodes.enrich.llm.chat_completion", AsyncMock(return_value=mock_response)):
        result = await enrich_candidates(state)

    assert len(result["enriched_candidates"]) == 1
    assert result["enriched_candidates"][0]["enriched_fields"] == {}


@pytest.mark.asyncio
async def test_enrich_empty_pool_returns_empty() -> None:
    state: PipelineState = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "reranked_candidates": [],
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    result = await enrich_candidates(state)
    assert result["enriched_candidates"] == []
