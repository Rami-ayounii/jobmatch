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


class EnrichedCandidate(TypedDict):
    candidate_id: str
    resume_text: str
    structured_jsonb: dict[str, Any]
    rerank_score: float
    enriched_fields: dict[str, Any]


class ShortlistEntry(TypedDict):
    rank: int
    candidate_id: str
    score: float
    justification: str
    citations: list[str]


class InviteDraft(TypedDict):
    candidate_id: str
    subject: str
    body: str


class ToolCallTrace(TypedDict):
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    call_index: int


class PipelineState(TypedDict, total=False):
    run_id: str
    tenant_id: str
    job_id: str
    user_id: str
    job_title: str
    job_description: str
    job_embedding: list[float]
    retrieved_candidates: list[RetrievedCandidate]
    reranked_candidates: list[RankedCandidate]
    enriched_candidates: list[EnrichedCandidate]
    rubric: dict[str, Any]
    shortlist: list[ShortlistEntry]
    validation_errors: list[str]
    loop_count: int
    draft_invites: list[InviteDraft]
    agent_trace: list[dict[str, Any]]
    total_cost_usd: float
    error: str | None
