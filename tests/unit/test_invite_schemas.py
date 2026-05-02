"""Unit tests for invites API Pydantic schemas."""
import uuid
from datetime import UTC, datetime
from app.api.invites import InviteOut, ApproveRequest, RejectRequest


def test_invite_out_minimal() -> None:
    inv = InviteOut(
        id=uuid.uuid4(),
        match_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        draft_subject="Interview Invitation",
        draft_body="Dear Candidate",
        status="awaiting_approval",
        created_at=datetime.now(UTC),
    )
    assert inv.status == "awaiting_approval"
    assert inv.approved_by is None


def test_invite_out_approved() -> None:
    inv = InviteOut(
        id=uuid.uuid4(),
        match_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        draft_subject="Interview Invitation",
        draft_body="Dear Candidate",
        status="approved",
        approved_by=uuid.uuid4(),
        approved_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    assert inv.approved_by is not None


def test_approve_request_defaults() -> None:
    r = ApproveRequest()
    assert r is not None


def test_reject_request_optional_reason() -> None:
    r = RejectRequest(reason=None)
    assert r.reason is None
    r2 = RejectRequest(reason="Not a fit")
    assert r2.reason == "Not a fit"
