"""Contract tests for validate_inputs pipeline node."""
import uuid
import pytest
from app.pipeline.nodes.validate import validate_inputs
from app.pipeline.state import PipelineState

TENANT_ID = str(uuid.uuid4())
JOB_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


@pytest.mark.asyncio
async def test_valid_state_passes() -> None:
    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_description": "We need a Python engineer.",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    result = await validate_inputs(state)
    assert result.get("error") is None


@pytest.mark.asyncio
async def test_missing_job_description_sets_error() -> None:
    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    result = await validate_inputs(state)
    assert result.get("error") is not None
    assert "job_description" in result["error"]


@pytest.mark.asyncio
async def test_invalid_tenant_uuid_sets_error() -> None:
    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": "not-a-uuid",
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    result = await validate_inputs(state)
    assert result.get("error") is not None


@pytest.mark.asyncio
async def test_valid_state_preserves_keys() -> None:
    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }
    result = await validate_inputs(state)
    assert result["job_id"] == JOB_ID
    assert result["tenant_id"] == TENANT_ID
