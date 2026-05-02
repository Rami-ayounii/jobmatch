"""Contract tests for embed_query pipeline node."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.nodes.embed import embed_query
from app.pipeline.state import PipelineState

TENANT_ID = str(uuid.uuid4())
JOB_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
FAKE_EMBEDDING = [0.1] * 1536


def _mock_job(title: str = "Python Engineer", description: str = "We need Python.") -> MagicMock:
    job = MagicMock()
    job.title = title
    job.description_text = description
    job.embedding = FAKE_EMBEDDING
    return job


@pytest.mark.asyncio
async def test_embed_query_loads_job_from_db() -> None:
    mock_repo = AsyncMock()
    mock_repo.get.return_value = _mock_job()

    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_description": "We need Python.",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }

    with (
        patch("app.pipeline.nodes.embed.AsyncSessionLocal") as mock_session_factory,
        patch("app.pipeline.nodes.embed.JobRepo", return_value=mock_repo),
    ):
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        begin = MagicMock()
        begin.__aenter__ = AsyncMock(return_value=None)
        begin.__aexit__ = AsyncMock(return_value=False)
        session.begin.return_value = begin
        session.execute = AsyncMock()
        mock_session_factory.return_value = session

        result = await embed_query(state)

    assert result["job_title"] == "Python Engineer"
    assert result["job_embedding"] == FAKE_EMBEDDING
    assert len(result["job_embedding"]) == 1536


@pytest.mark.asyncio
async def test_embed_query_job_not_found_sets_error() -> None:
    mock_repo = AsyncMock()
    mock_repo.get.return_value = None

    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_description": "We need Python.",
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }

    with (
        patch("app.pipeline.nodes.embed.AsyncSessionLocal") as mock_session_factory,
        patch("app.pipeline.nodes.embed.JobRepo", return_value=mock_repo),
    ):
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        begin = MagicMock()
        begin.__aenter__ = AsyncMock(return_value=None)
        begin.__aexit__ = AsyncMock(return_value=False)
        session.begin.return_value = begin
        session.execute = AsyncMock()
        mock_session_factory.return_value = session

        result = await embed_query(state)

    assert result.get("error") is not None
    assert "not found" in result["error"]
