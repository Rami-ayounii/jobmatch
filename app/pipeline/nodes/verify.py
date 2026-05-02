"""validate_output pipeline node: 8 deterministic checks + citation faithfulness."""
from __future__ import annotations

import re

from app.pipeline.state import PipelineState

_COST_CAP_USD = 1.50
_PII_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"  # email
    r"|(\+?\d[\d\s\-().]{7,}\d)"  # phone
)


async def validate_output(state: PipelineState) -> PipelineState:
    shortlist = state.get("shortlist", [])
    enriched = state.get("enriched_candidates", [])
    pool_ids = {c["candidate_id"] for c in enriched}
    errors: list[str] = []

    if len(shortlist) != 5:
        errors.append(f"shortlist must have 5 candidates, got {len(shortlist)}")

    seen_ids: set[str] = set()
    for entry in shortlist:
        if entry["candidate_id"] in seen_ids:
            errors.append(f"duplicate candidate_id in shortlist: {entry["candidate_id"]}")
        seen_ids.add(entry["candidate_id"])

    for entry in shortlist:
        if entry["candidate_id"] not in pool_ids:
            errors.append(f"candidate {entry["candidate_id"]} not in pool")

    for entry in shortlist:
        if not entry.get("justification", "").strip():
            errors.append(f"empty justification for candidate {entry["candidate_id"]}")

    resume_by_id = {c["candidate_id"]: c["resume_text"] for c in enriched}
    for entry in shortlist:
        resume_text = resume_by_id.get(entry["candidate_id"], "")
        for citation in entry.get("citations", []):
            if citation and citation not in resume_text:
                errors.append(
                    f"citation faithfulness FAIL for candidate {entry["candidate_id"]}: "
                    f"'{citation[:60]}' not found in resume"
                )

    for entry in shortlist:
        if not (0.0 <= entry.get("score", -1) <= 1.0):
            errors.append(f"score out of range for candidate {entry["candidate_id"]}: {entry.get("score")}")

    total_cost = state.get("total_cost_usd", 0.0)
    if total_cost > _COST_CAP_USD:
        errors.append(f"cost cap exceeded: ${total_cost:.4f} > ${_COST_CAP_USD}")

    for entry in shortlist:
        if _PII_PATTERN.search(entry.get("justification", "")):
            errors.append(f"PII detected in justification for candidate {entry["candidate_id"]}")

    loop_count = state.get("loop_count", 0)
    if errors:
        loop_count += 1

    return {**state, "validation_errors": errors, "loop_count": loop_count}
