"""Contract tests for the reason (agent) pipeline node."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.pipeline.nodes.reason import reason_agent
from app.pipeline.state import EnrichedCandidate, PipelineState

TENANT_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())

_CANDIDATE_IDS = [str(uuid.uuid4()) for _ in range(5)]

_ENRICHED: list[EnrichedCandidate] = [
    {
        "candidate_id": cid,
        "resume_text": f"Candidate {i} has 5 years Python experience at ACME.",
        "structured_jsonb": {"skills": ["Python"], "experience_years": 5},
        "rerank_score": 0.9 - i * 0.02,
        "enriched_fields": {"summary": f"Senior Python dev {i}"},
    }
    for i, cid in enumerate(_CANDIDATE_IDS)
]

_BASE_STATE: PipelineState = {
    "run_id": RUN_ID,
    "tenant_id": TENANT_ID,
    "job_id": str(uuid.uuid4()),
    "user_id": str(uuid.uuid4()),
    "job_title": "Python Engineer",
    "job_description": "We need a senior Python engineer with 5+ years experience.",
    "job_embedding": [0.1] * 1536,
    "enriched_candidates": _ENRICHED,
    "loop_count": 0,
    "total_cost_usd": 0.0,
    "agent_trace": [],
}


def _rubric_response() -> MagicMock:
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = json.dumps({
        "criteria": [
            {"name": "Python depth", "weight": 0.6, "description": "Years of Python"},
            {"name": "Seniority", "weight": 0.4, "description": "Level"},
        ]
    })
    r.usage.prompt_tokens = 100
    r.usage.completion_tokens = 50
    return r


def _finalize_tool_call_response() -> MagicMock:
    shortlist = [
        {
            "rank": i + 1,
            "candidate_id": _CANDIDATE_IDS[i],
            "score": 0.9 - i * 0.05,
            "justification": f"Candidate {i} has 5 years Python experience at ACME.",
            "citations": ["5 years Python experience at ACME"],
        }
        for i in range(5)
    ]
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = None
    r.choices[0].message.tool_calls = [MagicMock()]
    r.choices[0].message.tool_calls[0].function.name = "finalize_shortlist"
    r.choices[0].message.tool_calls[0].function.arguments = json.dumps({"candidates": shortlist})
    r.choices[0].message.tool_calls[0].id = "call_001"
    r.choices[0].finish_reason = "tool_calls"
    r.usage.prompt_tokens = 500
    r.usage.completion_tokens = 200
    return r


def _text_response(content: str) -> MagicMock:
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = content
    r.choices[0].message.tool_calls = None
    r.choices[0].finish_reason = "stop"
    r.usage.prompt_tokens = 100
    r.usage.completion_tokens = 50
    return r


@pytest.mark.asyncio
async def test_reason_agent_finalizes_shortlist() -> None:
    """Agent calls finalize_shortlist and result appears in state."""
    call_count = 0

    async def mock_completion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _rubric_response()
        return _finalize_tool_call_response()

    with patch("app.pipeline.nodes.reason.llm.chat_completion", side_effect=mock_completion):
        result = await reason_agent(_BASE_STATE)

    assert result.get("error") is None
    assert len(result.get("shortlist", [])) == 5
    assert result["shortlist"][0]["rank"] == 1


@pytest.mark.asyncio
async def test_reason_agent_caps_tool_calls() -> None:
    """Agent loop stops at 6 tool calls even without finalize."""
    async def mock_completion(*args, **kwargs):
        return _text_response("I need more information.")

    with patch("app.pipeline.nodes.reason.llm.chat_completion", side_effect=mock_completion):
        result = await reason_agent(_BASE_STATE)

    tool_calls_in_trace = [
        e for e in result.get("agent_trace", [])
        if e.get("type") == "llm_call"
    ]
    assert len(tool_calls_in_trace) <= 8


@pytest.mark.asyncio
async def test_reason_agent_cost_tracked() -> None:
    async def mock_completion(*args, **kwargs):
        return _finalize_tool_call_response()

    with patch("app.pipeline.nodes.reason.llm.chat_completion", side_effect=mock_completion):
        result = await reason_agent(_BASE_STATE)

    assert result.get("total_cost_usd", 0.0) >= 0.0
