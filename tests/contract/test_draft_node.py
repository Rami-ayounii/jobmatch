"""Contract tests for draft_invites pipeline node."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.pipeline.nodes.draft import draft_invites
from app.pipeline.state import EnrichedCandidate, PipelineState, ShortlistEntry

TENANT_ID = str(uuid.uuid4())
_CIDS = [str(uuid.uuid4()) for _ in range(5)]

_ENRICHED: list[EnrichedCandidate] = [
    {
        "candidate_id": cid,
        "resume_text": f"Candidate {i} resume",
        "structured_jsonb": {"seniority_level": "senior", "skills": ["Python"]},
        "rerank_score": 0.9,
        "enriched_fields": {},
    }
    for i, cid in enumerate(_CIDS)
]

_SHORTLIST: list[ShortlistEntry] = [
    {
        "rank": i + 1,
        "candidate_id": _CIDS[i],
        "score": 0.9 - i * 0.05,
        "justification": f"Strong candidate {i}",
        "citations": [],
    }
    for i in range(5)
]

_DRAFT_JSON = json.dumps({"subject": "Interview invitation", "body": "Dear Candidate, we'd love to meet you."})


def _mock_llm_response() -> MagicMock:
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = _DRAFT_JSON
    r.usage.prompt_tokens = 100
    r.usage.completion_tokens = 80
    return r


@pytest.mark.asyncio
async def test_draft_invites_produces_five_drafts() -> None:
    state: PipelineState = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_title": "Python Engineer",
        "job_description": "We need a senior Python engineer.",
        "enriched_candidates": _ENRICHED,
        "shortlist": _SHORTLIST,
        "loop_count": 0,
        "total_cost_usd": 0.3,
        "agent_trace": [],
    }
    with patch("app.pipeline.nodes.draft.llm.chat_completion", AsyncMock(return_value=_mock_llm_response())):
        result = await draft_invites(state)

    assert len(result["draft_invites"]) == 5
    assert result["draft_invites"][0]["subject"] == "Interview invitation"
    assert result["draft_invites"][0]["candidate_id"] == _CIDS[0]


@pytest.mark.asyncio
async def test_draft_invites_handles_parse_failure() -> None:
    bad_response = MagicMock()
    bad_response.choices = [MagicMock()]
    bad_response.choices[0].message.content = "bad json {"
    bad_response.usage.prompt_tokens = 10
    bad_response.usage.completion_tokens = 5

    state: PipelineState = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_title": "Python Engineer",
        "job_description": "We need Python.",
        "enriched_candidates": _ENRICHED[:1],
        "shortlist": _SHORTLIST[:1],
        "loop_count": 0,
        "total_cost_usd": 0.1,
        "agent_trace": [],
    }
    with patch("app.pipeline.nodes.draft.llm.chat_completion", AsyncMock(return_value=bad_response)):
        result = await draft_invites(state)

    assert len(result["draft_invites"]) == 1
    assert "Interview Invitation" in result["draft_invites"][0]["subject"]
