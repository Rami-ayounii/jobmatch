# tests/integration/test_candidates_api.py
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest


def _pdf(content: bytes = b"%PDF-1.4 stub") -> dict:
    return {"file": ("resume.pdf", io.BytesIO(content), "application/pdf")}


@pytest.mark.asyncio
async def test_post_candidate_returns_202(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        resp = await client.post("/candidates", data=unique_form, files=_pdf())
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "processing"
    uuid.UUID(body["id"])  # valid UUID


@pytest.mark.asyncio
async def test_post_candidate_dedup_returns_200(client, unique_form):
    with patch("app.api.candidates.process_resume") as mock_task, \
         patch("app.api.candidates.storage.upload_resume", return_value="t/c/r.pdf"):
        mock_task.delay = lambda *a, **kw: None
        r1 = await client.post("/candidates", data=unique_form, files=_pdf())
        r2 = await client.post("/candidates", data=unique_form, files=_pdf())
    assert r1.status_code == 202
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
