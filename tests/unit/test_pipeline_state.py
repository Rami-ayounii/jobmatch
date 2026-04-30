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
