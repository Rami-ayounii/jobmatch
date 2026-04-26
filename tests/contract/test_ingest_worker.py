"""Contract tests for ingest_worker — mocked I/O, no DB or network."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.ingest_worker import _run_delete_r2_object, _run_process_resume

TENANT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
CANDIDATE_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000002"))
RUN_ID = "run-test-001"
R2_KEY = "00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/resume.pdf"

_CONTACT_BYTES = json.dumps({
    "name": "Jane Doe",
    "email": "jane@example.com",
    "consent_ip": "1.2.3.4",
    "phone": None,
    "address": None,
}).encode()

_PARSED_JSON = json.dumps({
    "skills": ["Python", "FastAPI"],
    "experience_years": 3,
    "education": [],
    "languages": ["English"],
    "location": "Paris, France",
    "seniority_level": "mid",
    "job_titles": ["Software Engineer"],
    "certifications": [],
    "phone": "+33612345678",
    "address": "1 Rue de Paris",
})

_FAKE_EMBEDDING = [0.1] * 1536


def _make_candidate(already_parsed: bool = False) -> MagicMock:
    c = MagicMock()
    c.r2_key = R2_KEY
    c.contact_jsonb_encrypted = _CONTACT_BYTES  # crypto is mocked out
    c.embedding = _FAKE_EMBEDDING if already_parsed else None
    return c


def _make_session_factory(candidate_repo: AsyncMock, audit_repo: AsyncMock) -> MagicMock:
    """Return a mock AsyncSessionLocal and wire in the repo mocks."""
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin_cm

    return MagicMock(return_value=session)


def _llm_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_process_resume_happy_path() -> None:
    """Normal run: candidate found, not yet embedded → parse + embed + update DB."""
    candidate = _make_candidate(already_parsed=False)
    candidate_repo = AsyncMock()
    candidate_repo.get.return_value = candidate
    audit_repo = AsyncMock()

    session_factory = _make_session_factory(candidate_repo, audit_repo)

    with (
        patch("app.workers.ingest_worker.AsyncSessionLocal", session_factory),
        patch("app.workers.ingest_worker.CandidateRepo", return_value=candidate_repo),
        patch("app.workers.ingest_worker.AuditRepo", return_value=audit_repo),
        patch("app.workers.ingest_worker.decrypt_bytes", lambda b: b),
        patch("app.workers.ingest_worker.encrypt_bytes", lambda b: b),
        patch(
            "app.workers.ingest_worker.storage.download_resume",
            AsyncMock(return_value=b"%PDF-fake"),
        ),
        patch(
            "app.workers.ingest_worker.resume_parser.extract_text",
            AsyncMock(return_value="Jane Doe — Software Engineer"),
        ),
        patch(
            "app.workers.ingest_worker.llm.chat_completion",
            AsyncMock(return_value=_llm_response(_PARSED_JSON)),
        ),
        patch(
            "app.workers.ingest_worker.embeddings.embed_text",
            AsyncMock(return_value=_FAKE_EMBEDDING),
        ),
    ):
        await _run_process_resume(RUN_ID, CANDIDATE_ID, TENANT_ID)

    candidate_repo.update_after_parse.assert_awaited_once()
    call_kwargs = candidate_repo.update_after_parse.call_args.kwargs
    assert call_kwargs["resume_text"] == "Jane Doe — Software Engineer"
    assert call_kwargs["structured_jsonb"]["seniority_level"] == "mid"
    assert call_kwargs["embedding"] == _FAKE_EMBEDDING

    # contact blob must have phone/address merged in (encrypt_bytes mocked to passthrough)
    contact = json.loads(call_kwargs["contact_jsonb_encrypted"])
    assert contact["phone"] == "+33612345678"
    assert contact["address"] == "1 Rue de Paris"

    audit_repo.log.assert_awaited_once()
    log_kwargs = audit_repo.log.call_args
    assert log_kwargs.args[1] == "resume_parsed"


@pytest.mark.asyncio
async def test_process_resume_idempotent() -> None:
    """Candidate already has embedding → no download/parse/embed should happen."""
    candidate = _make_candidate(already_parsed=True)
    candidate_repo = AsyncMock()
    candidate_repo.get.return_value = candidate
    audit_repo = AsyncMock()

    session_factory = _make_session_factory(candidate_repo, audit_repo)
    download_mock = AsyncMock()

    with (
        patch("app.workers.ingest_worker.AsyncSessionLocal", session_factory),
        patch("app.workers.ingest_worker.CandidateRepo", return_value=candidate_repo),
        patch("app.workers.ingest_worker.AuditRepo", return_value=audit_repo),
        patch("app.workers.ingest_worker.storage.download_resume", download_mock),
    ):
        await _run_process_resume(RUN_ID, CANDIDATE_ID, TENANT_ID)

    download_mock.assert_not_awaited()
    candidate_repo.update_after_parse.assert_not_awaited()
    audit_repo.log.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_resume_parse_error_fallback() -> None:
    """LLM parse failure → structured_jsonb is empty but embedding still stored."""
    candidate = _make_candidate(already_parsed=False)
    candidate_repo = AsyncMock()
    candidate_repo.get.return_value = candidate
    audit_repo = AsyncMock()

    session_factory = _make_session_factory(candidate_repo, audit_repo)

    with (
        patch("app.workers.ingest_worker.AsyncSessionLocal", session_factory),
        patch("app.workers.ingest_worker.CandidateRepo", return_value=candidate_repo),
        patch("app.workers.ingest_worker.AuditRepo", return_value=audit_repo),
        patch("app.workers.ingest_worker.decrypt_bytes", lambda b: b),
        patch("app.workers.ingest_worker.encrypt_bytes", lambda b: b),
        patch(
            "app.workers.ingest_worker.storage.download_resume",
            AsyncMock(return_value=b"%PDF-fake"),
        ),
        patch(
            "app.workers.ingest_worker.resume_parser.extract_text",
            AsyncMock(return_value="Jane Doe — Software Engineer"),
        ),
        patch(
            "app.workers.ingest_worker.llm.chat_completion",
            AsyncMock(side_effect=RuntimeError("Groq timeout")),
        ),
        patch(
            "app.workers.ingest_worker.embeddings.embed_text",
            AsyncMock(return_value=_FAKE_EMBEDDING),
        ),
    ):
        await _run_process_resume(RUN_ID, CANDIDATE_ID, TENANT_ID)

    candidate_repo.update_after_parse.assert_awaited_once()
    call_kwargs = candidate_repo.update_after_parse.call_args.kwargs
    assert call_kwargs["structured_jsonb"] == {}
    assert call_kwargs["embedding"] == _FAKE_EMBEDDING
    audit_repo.log.assert_awaited_once()
