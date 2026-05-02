"""Unit tests for matches API Pydantic schemas."""
import uuid
from datetime import UTC, datetime
from app.api.matches import MatchRunRequest, MatchRunResponse, MatchStatusResponse


def test_match_run_request() -> None:
    r = MatchRunRequest(job_id=uuid.uuid4())
    assert r.job_id is not None


def test_match_run_response() -> None:
    r = MatchRunResponse(run_id=uuid.uuid4(), status="pending")
    assert r.status == "pending"


def test_match_status_response_minimal() -> None:
    r = MatchStatusResponse(
        run_id=uuid.uuid4(),
        status="running",
        job_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    assert r.shortlist is None
    assert r.cost_usd is None
