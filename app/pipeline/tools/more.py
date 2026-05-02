"""request_more_candidates tool — returns additional candidates from the reranked pool."""
from __future__ import annotations

from app.pipeline.state import PipelineState
from app.pipeline.tools import ToolResult


def request_more_candidates(state: PipelineState, n: int) -> ToolResult:
    if n < 1 or n > 10:
        return ToolResult(success=False, data=None, error="n must be between 1 and 10")

    enriched_ids = {c["candidate_id"] for c in state.get("enriched_candidates", [])}
    pool = state.get("reranked_candidates", [])
    additional = [c for c in pool if c["candidate_id"] not in enriched_ids][:n]

    return ToolResult(
        success=True,
        data={
            "candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "resume_text": c["resume_text"][:1000],
                    "structured_jsonb": c["structured_jsonb"],
                    "rerank_score": c["rerank_score"],
                }
                for c in additional
            ]
        },
    )
