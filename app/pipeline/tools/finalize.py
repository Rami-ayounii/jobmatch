"""finalize_shortlist tool — submits the agent's final top-5 shortlist."""
from __future__ import annotations

from app.pipeline.state import PipelineState, ShortlistEntry
from app.pipeline.tools import ToolResult


def finalize_shortlist(
    state: PipelineState,
    candidates: list[dict],
) -> ToolResult:
    if len(candidates) != 5:
        return ToolResult(
            success=False,
            data=None,
            error=f"shortlist must contain exactly 5 candidates, got {len(candidates)}",
        )

    shortlist: list[ShortlistEntry] = []
    for entry in candidates:
        shortlist.append(
            ShortlistEntry(
                rank=entry["rank"],
                candidate_id=entry["candidate_id"],
                score=float(entry["score"]),
                justification=entry["justification"],
                citations=entry.get("citations", []),
            )
        )

    return ToolResult(success=True, data={"shortlist": shortlist})
