"""Unit tests for Phase 4 PipelineState TypedDicts."""
from app.pipeline.state import (
    EnrichedCandidate,
    InviteDraft,
    PipelineState,
    ShortlistEntry,
    ToolCallTrace,
)


def test_enriched_candidate_keys() -> None:
    c: EnrichedCandidate = {
        "candidate_id": "abc",
        "resume_text": "resume",
        "structured_jsonb": {"skills": ["Python"]},
        "rerank_score": 0.9,
        "enriched_fields": {"years_experience": 5},
    }
    assert c["rerank_score"] == 0.9
    assert c["enriched_fields"]["years_experience"] == 5


def test_shortlist_entry_keys() -> None:
    e: ShortlistEntry = {
        "rank": 1,
        "candidate_id": "abc",
        "score": 0.95,
        "justification": "Strong Python background",
        "citations": ["5 years Python experience"],
    }
    assert e["rank"] == 1
    assert len(e["citations"]) == 1


def test_invite_draft_keys() -> None:
    d: InviteDraft = {
        "candidate_id": "abc",
        "subject": "Interview invitation",
        "body": "Dear Candidate, ...",
    }
    assert d["subject"] == "Interview invitation"


def test_tool_call_trace_keys() -> None:
    t: ToolCallTrace = {
        "tool_name": "get_candidate_profile",
        "args": {"candidate_id": "abc"},
        "result": {"success": True, "data": {}},
        "call_index": 0,
    }
    assert t["tool_name"] == "get_candidate_profile"


def test_pipeline_state_phase4_fields() -> None:
    state: PipelineState = {
        "run_id": "r1",
        "tenant_id": "t1",
        "job_id": "j1",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    assert state["loop_count"] == 0
    assert state["total_cost_usd"] == 0.0
