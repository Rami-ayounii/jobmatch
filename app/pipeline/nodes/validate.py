"""validate_inputs pipeline node."""
from __future__ import annotations

import uuid

from app.pipeline.state import PipelineState


def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def validate_inputs(state: PipelineState) -> PipelineState:
    for field in ("run_id", "tenant_id", "job_id", "user_id"):
        value = state.get(field)
        if not value:
            return {**state, "error": f"missing required field: {field}"}
        if field in ("tenant_id", "job_id", "run_id", "user_id") and not _is_valid_uuid(value):
            return {**state, "error": f"invalid UUID for field: {field}"}

    if not state.get("job_description", "").strip():
        return {**state, "error": "job_description is required and must not be empty"}

    return {**state, "error": None}
