"""Rerank pipeline node: BGE reranker reduces top-50 → top-15."""
from __future__ import annotations

from app.pipeline.state import PipelineState, RankedCandidate
from app.services import reranker

_TOP_N = 15


async def rerank_candidates(state: PipelineState) -> PipelineState:
    candidates = state.get("retrieved_candidates", [])
    if not candidates:
        return {**state, "reranked_candidates": []}

    passages = [c["resume_text"] for c in candidates]
    scores = await reranker.rerank(state["job_description"], passages)

    paired = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    top = paired[:_TOP_N]

    ranked: list[RankedCandidate] = [
        RankedCandidate(
            candidate_id=c["candidate_id"],
            resume_text=c["resume_text"],
            structured_jsonb=c["structured_jsonb"],
            rerank_score=float(score),
        )
        for c, score in top
    ]

    return {**state, "reranked_candidates": ranked}
