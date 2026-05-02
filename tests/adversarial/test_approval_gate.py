"""Adversarial: the approval gate cannot be bypassed.

Verifies that send_worker refuses to send any invite that was not
explicitly approved via the POST /invites/{id}/approve endpoint —
even if someone manipulates status directly or calls the worker early.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

TENANT_ID = str(uuid.uuid4())
INVITE_ID = str(uuid.uuid4())


def _make_session_with_invite(status: str, approved_by: uuid.UUID | None) -> tuple[MagicMock, MagicMock]:
    invite = MagicMock()
    invite.id = uuid.UUID(INVITE_ID)
    invite.status = status
    invite.approved_by = approved_by

    invite_repo = AsyncMock()
    invite_repo.get.return_value = invite

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin
    session.execute = AsyncMock()
    return session, invite_repo


@pytest.mark.asyncio
async def test_worker_blocked_when_status_is_awaiting_approval() -> None:
    """send_worker must not send when invite is still awaiting approval."""
    from app.workers.send_worker import _dispatch

    session, invite_repo = _make_session_with_invite("awaiting_approval", None)
    send_mock = AsyncMock()

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", AsyncMock()),
        patch("app.db.repos.AuditRepo", AsyncMock()),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_blocked_when_status_is_rejected() -> None:
    """send_worker must not send a rejected invite."""
    from app.workers.send_worker import _dispatch

    session, invite_repo = _make_session_with_invite("rejected", None)
    send_mock = AsyncMock()

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", AsyncMock()),
        patch("app.db.repos.AuditRepo", AsyncMock()),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_blocked_when_approved_by_null_despite_approved_status() -> None:
    """Even if status=approved, approved_by IS NULL must block sending.

    This simulates a direct DB write or corruption bypassing the approval
    endpoint, which always sets approved_by via InviteRepo.approve().
    """
    from app.workers.send_worker import _dispatch

    session, invite_repo = _make_session_with_invite("approved", None)
    send_mock = AsyncMock()

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", AsyncMock()),
        patch("app.db.repos.AuditRepo", AsyncMock()),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_blocked_when_already_sent() -> None:
    """send_worker must not re-send an already-sent invite (idempotency guard)."""
    from app.workers.send_worker import _dispatch

    session, invite_repo = _make_session_with_invite("sent", uuid.uuid4())
    send_mock = AsyncMock()

    with (
        patch("app.db.session.AsyncSessionLocal", return_value=session),
        patch("app.db.repos.InviteRepo", return_value=invite_repo),
        patch("app.db.repos.CandidateRepo", AsyncMock()),
        patch("app.db.repos.AuditRepo", AsyncMock()),
        patch("app.services.email.send_email", send_mock),
    ):
        await _dispatch(INVITE_ID, TENANT_ID)

    send_mock.assert_not_awaited()
