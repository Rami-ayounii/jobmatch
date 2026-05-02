"""Unit tests for agent tools."""
import uuid
import pytest
from app.pipeline.state import EnrichedCandidate, PipelineState, ShortlistEntry
from app.pipeline.tools import ToolResult
from app.pipeline.tools.candidate import get_candidate_profile
from app.pipeline.tools.finalize import finalize_shortlist
from app.pipeline.tools.more import request_more_candidates
from app.pipeline.tools.rescore import rescore_candidate

TENANT_ID = str(uuid.uuid4())

_CANDIDATE_ID = str(uuid.uuid4())

_ENRICHED: list[EnrichedCandidate] = [
    {
        "candidate_id": _CANDIDATE_ID,
        "resume_text": "Alice has 7 years Python experience at ACME Corp.",
        "structured_jsonb": {"skills": ["Python"], "experience_years": 7},
        "rerank_score": 0.9,
        "enriched_fields": {"summary": "Senior Python dev"},
    }
]

_BASE_STATE: PipelineState = {
    "run_id": str(uuid.uuid4()),
    "tenant_id": TENANT_ID,
    "job_id": str(uuid.uuid4()),
    "user_id": str(uuid.uuid4()),
    "job_description": "Python engineer",
    "enriched_candidates": _ENRICHED,
    "reranked_candidates": [],
    "loop_count": 0,
    "total_cost_usd": 0.0,
    "agent_trace": [],
}


def test_tool_result_success() -> None:
    r = ToolResult(success=True, data={"key": "value"}, error=None)
    assert r.success is True
    assert r.data["key"] == "value"


def test_tool_result_error() -> None:
    r = ToolResult(success=False, data=None, error="not found")
    assert r.success is False
    assert r.error == "not found"


def test_get_candidate_profile_found() -> None:
    result = get_candidate_profile(_BASE_STATE, _CANDIDATE_ID)
    assert result.success is True
    assert result.data["candidate_id"] == _CANDIDATE_ID
    assert result.data["enriched_fields"]["summary"] == "Senior Python dev"


def test_get_candidate_profile_not_found() -> None:
    result = get_candidate_profile(_BASE_STATE, str(uuid.uuid4()))
    assert result.success is False
    assert "not found" in result.error


def test_rescore_candidate_adds_trace() -> None:
    result = rescore_candidate(
        _BASE_STATE,
        candidate_id=_CANDIDATE_ID,
        criterion="Python depth",
        score=0.95,
        justification="7 years Python experience at ACME Corp.",
    )
    assert result.success is True
    assert result.data["candidate_id"] == _CANDIDATE_ID
    assert result.data["score"] == 0.95


def test_rescore_candidate_score_out_of_range() -> None:
    result = rescore_candidate(
        _BASE_STATE,
        candidate_id=_CANDIDATE_ID,
        criterion="Python depth",
        score=1.5,
        justification="test",
    )
    assert result.success is False
    assert "range" in result.error


def test_request_more_candidates_returns_from_pool() -> None:
    extra: list[EnrichedCandidate] = [
        {
            "candidate_id": str(uuid.uuid4()),
            "resume_text": f"Extra candidate {i}",
            "structured_jsonb": {},
            "rerank_score": 0.5,
            "enriched_fields": {},
        }
        for i in range(5)
    ]
    state = {**_BASE_STATE, "reranked_candidates": [
        {"candidate_id": c["candidate_id"], "resume_text": c["resume_text"],
         "structured_jsonb": {}, "rerank_score": 0.5}
        for c in extra
    ]}
    result = request_more_candidates(state, n=3)
    assert result.success is True
    assert len(result.data["candidates"]) == 3


def test_finalize_shortlist_valid() -> None:
    entries = [
        {
            "rank": i + 1,
            "candidate_id": str(uuid.uuid4()),
            "score": 0.9 - i * 0.05,
            "justification": f"Strong candidate {i}",
            "citations": [f"5 years experience {i}"],
        }
        for i in range(5)
    ]
    result = finalize_shortlist(_BASE_STATE, entries)
    assert result.success is True
    assert len(result.data["shortlist"]) == 5


def test_finalize_shortlist_wrong_count() -> None:
    entries = [
        {"rank": 1, "candidate_id": str(uuid.uuid4()), "score": 0.9,
         "justification": "good", "citations": ["text"]}
    ]
    result = finalize_shortlist(_BASE_STATE, entries)
    assert result.success is False
    assert "5" in result.error
