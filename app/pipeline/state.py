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
