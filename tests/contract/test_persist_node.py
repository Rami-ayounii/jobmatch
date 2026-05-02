"""Contract tests for persist_results pipeline node."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.pipeline.nodes.persist import persist_results
from app.pipeline.state import InviteDraft, PipelineState, ShortlistEntry

TENANT_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
JOB_ID = str(uuid.uuid4())
MATCH_ID = str(uuid.uuid4())
_CIDS = [str(uuid.uuid4()) for _ in range(5)]

_SHORTLIST: list[ShortlistEntry] = [
    {"rank": i + 1, "candidate_id": _CIDS[i], "score": 0.9, "justification": "good", "citations": []}
    for i in range(5)
]

_DRAFTS: list[InviteDraft] = [
    {"candidate_id": _CIDS[i], "subject": "Interview", "body": "Dear Candidate"}
    for i in range(5)
]


def _make_session_context(match_id: str = MATCH_ID) -> tuple[MagicMock, MagicMock, MagicMock]:
    match_repo = AsyncMock()
    invite_repo = AsyncMock()
    audit_repo = AsyncMock()

    mock_match = MagicMock()
    mock_match.id = uuid.UUID(match_id)
    match_repo.get.return_value = mock_match

    return match_repo, invite_repo, audit_repo


@pytest.mark.asyncio
async def test_persist_updates_match_status() -> None:
    match_repo, invite_repo, audit_repo = _make_session_context()

    state: PipelineState = {
        "run_id": RUN_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "user_id": str(uuid.uuid4()),
        "job_title": "Python Engineer",
        "job_description": "Python dev",
        "shortlist": _SHORTLIST,
        "draft_invites": _DRAFTS,
        "agent_trace": [{"type": "test"}],
        "total_cost_usd": 0.25,
        "loop_count": 0,
        "validation_errors": [],
    }

    with (
        patch("app.pipeline.nodes.persist.AsyncSessionLocal") as mock_session_factory,
        patch("app.pipeline.nodes.persist.MatchRepo", return_value=match_repo),
        patch("app.pipeline.nodes.persist.InviteRepo", return_value=invite_repo),
        patch("app.pipeline.nodes.persist.AuditRepo", return_value=audit_repo),
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

        result = await persist_results(state)

    match_repo.update_status.assert_awaited_once()
    call_kwargs = match_repo.update_status.call_args.kwargs
    assert call_kwargs["status"] == "completed"
    assert call_kwargs["cost_usd"] == pytest.approx(0.25)
    assert result is not None
