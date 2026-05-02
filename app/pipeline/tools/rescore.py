"""rescore_candidate tool — records a score adjustment in the trace."""
from __future__ import annotations

from app.pipeline.state import PipelineState
from app.pipeline.tools import ToolResult


def rescore_candidate(
    state: PipelineState,
    candidate_id: str,
    criterion: str,
    score: float,
    justification: str,
) -> ToolResult:
    if not (0.0 <= score <= 1.0):
        return ToolResult(success=False, data=None, error="score must be in range [0.0, 1.0]")

    enriched = state.get("enriched_candidates", [])
    if not any(c["candidate_id"] == candidate_id for c in enriched):
        return ToolResult(success=False, data=None, error=f"candidate {candidate_id} not found")

    return ToolResult(
        success=True,
        data={
            "candidate_id": candidate_id,
            "criterion": criterion,
            "score": score,
            "justification": justification,
        },
    )
