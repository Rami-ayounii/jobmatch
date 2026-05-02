"""get_candidate_profile tool."""
from __future__ import annotations

from app.pipeline.state import PipelineState
from app.pipeline.tools import ToolResult


def get_candidate_profile(state: PipelineState, candidate_id: str) -> ToolResult:
    enriched = state.get("enriched_candidates", [])
    for c in enriched:
        if c["candidate_id"] == candidate_id:
            return ToolResult(
                success=True,
                data={
                    "candidate_id": c["candidate_id"],
                    "structured_jsonb": c["structured_jsonb"],
                    "enriched_fields": c["enriched_fields"],
                    "rerank_score": c["rerank_score"],
                },
            )
    return ToolResult(success=False, data=None, error=f"candidate {candidate_id} not found in pool")
