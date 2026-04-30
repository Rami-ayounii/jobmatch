"""Unit tests for Phase 3 schemas."""
import uuid
from datetime import UTC, datetime

import pytest

from app.api.jobs import JobCreate, JobResponse
from app.db.repos import CandidateRetrievalResult


def test_candidate_retrieval_result_fields() -> None:
    cid = uuid.uuid4()
    r = CandidateRetrievalResult(
        candidate_id=cid,
        resume_text="Alice resume",
        structured_jsonb={"skills": ["Python"]},
        cosine_score=0.9,
        text_score=0.7,
        combined_score=0.84,
    )
    assert r.candidate_id == cid
    assert r.combined_score == pytest.approx(0.84)


def test_combined_score_range() -> None:
    r = CandidateRetrievalResult(
        candidate_id=uuid.uuid4(),
        resume_text="x",
        structured_jsonb={},
        cosine_score=1.0,
        text_score=1.0,
        combined_score=1.0,
    )
    assert 0.0 <= r.combined_score <= 1.0


def test_job_create_model() -> None:
    j = JobCreate(title="Senior Python Engineer", description_text="We need Python skills.")
    assert j.title == "Senior Python Engineer"


def test_job_response_model() -> None:
    r = JobResponse(
        id=uuid.uuid4(),
        title="Senior Python Engineer",
        status="active",
        created_at=datetime.now(UTC),
    )
    assert r.status == "active"
