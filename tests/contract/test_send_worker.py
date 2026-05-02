"""Contract tests for send_worker — safety invariants."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

TENANT_ID = str(uuid.uuid4())
INVITE_ID = str(uuid.uuid4())
CANDIDATE_ID = str(uuid.uuid4())


def _mock_invite(
    status: str = "approved",
    approved_by: uuid.UUID | None = None,
    candidate_id: str = CANDIDATE_ID,
) -> MagicMock:
    inv = MagicMock()
    inv.id = uuid.UUID(INVITE_ID)
    inv.status = status
    inv.approved_by = approved_by or uuid.uuid4()
    inv.candidate_id = uuid.UUID(candidate_id)
    inv.draft_subject = "Interview Invitation"
    inv.draft_body = "Dear Candidate, please come to interview."
    return inv


CONTACT_EMAIL = "alice@example.com"
_CONTACT_BYTES = json.dumps({"name": "Alice", "email": CONTACT_EMAIL}).encode()


def _mock_candidate() -> MagicMock:
    c = MagicMock()
    c.contact_jsonb_encrypted = b"encrypted-placeholder"
    return c


def _make_repos_and_session(
    invite: MagicMock, candidate: MagicMock
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    invite_repo = AsyncMock()
    invite_repo.get.return_value = invite
    candidate_repo = AsyncMock()
    candidate_repo.get.return_value = candidate
    audit_repo = AsyncMock()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin
    session.execute = AsyncMock()
    return session, invite_repo, candidate_repo, audit_repo


def _patchers(session: MagicMock, invite_repo: MagicMock, candidate_repo: MagicMock, audit_repo: MagicMock, send_mock: AsyncMock) -> list:  # type: ignore[type-arg]
    """Return patch context managers targeting source modules (imports are deferred inside _dispatch)."""
    return [
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", return_value=candidate_repo),
        patch("app.db.repos.AuditRepo", return_value=audit_repo),
        patch("app.services.email.send_email", send_mock),
    ]


@pytest.mark.asyncio
async def test_dispatch_sends_approved_invite() -> None:
    """Happy path: approved invite with approved_by → email sent."""
    from app.workers.send_worker import _dispatch

    invite = _mock_invite(status="approved")
    candidate = _mock_candidate()
    session, invite_repo, candidate_repo, audit_repo = _make_repos_and_session(invite, candidate)
    send_mock = AsyncMock(return_value="msg_123")

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", return_value=candidate_repo),
        patch("app.db.repos.AuditRepo", return_value=audit_repo),
        patch("app.core.security.decrypt_bytes", return_value=_CONTACT_BYTES),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    invite_repo.mark_sent.assert_awaited_once()
    # mark_sent(tid, iid, resend_message_id) — positional call
    assert invite_repo.mark_sent.call_args.args[2] == "msg_123"


@pytest.mark.asyncio
async def test_dispatch_refuses_awaiting_approval() -> None:
    """SAFETY: invite still awaiting_approval must not be sent."""
    from app.workers.send_worker import _dispatch

    invite = _mock_invite(status="awaiting_approval")
    candidate = _mock_candidate()
    session, invite_repo, candidate_repo, audit_repo = _make_repos_and_session(invite, candidate)
    send_mock = AsyncMock(return_value="should_not_be_called")

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", return_value=candidate_repo),
        patch("app.db.repos.AuditRepo", return_value=audit_repo),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()
    invite_repo.mark_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_refuses_null_approved_by() -> None:
    """SAFETY: approved_by IS NULL must block sending even if status=approved."""
    from app.workers.send_worker import _dispatch

    invite = _mock_invite(status="approved")
    invite.approved_by = None  # simulate DB corruption / direct write
    candidate = _mock_candidate()
    session, invite_repo, candidate_repo, audit_repo = _make_repos_and_session(invite, candidate)
    send_mock = AsyncMock(return_value="should_not_be_called")

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", return_value=candidate_repo),
        patch("app.db.repos.AuditRepo", return_value=audit_repo),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()
    invite_repo.mark_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_marks_failed_on_send_error() -> None:
    """If Resend raises, invite is marked failed not left in approved."""
    from app.workers.send_worker import _dispatch

    invite = _mock_invite(status="approved")
    candidate = _mock_candidate()
    session, invite_repo, candidate_repo, audit_repo = _make_repos_and_session(invite, candidate)

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", return_value=candidate_repo),
        patch("app.db.repos.AuditRepo", return_value=audit_repo),
        patch("app.core.security.decrypt_bytes", return_value=_CONTACT_BYTES),
        patch(
            "app.services.email.send_email",
            AsyncMock(side_effect=RuntimeError("Resend down")),
        ),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    invite_repo.mark_failed.assert_awaited_once()
    invite_repo.mark_sent.assert_not_awaited()
