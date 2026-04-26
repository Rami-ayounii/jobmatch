"""Adversarial tests for POST /candidates — boundary conditions and abuse cases."""
from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_oversized_file_returns_413(client: AsyncClient) -> None:
    """Files over 5 MB must be rejected before any processing."""
    big_pdf = b"%PDF-1.4 " + b"X" * (5 * 1024 * 1024 + 1)
    response = await client.post(
        "/candidates",
        data={
            "name": "Big Boss",
            "email": "bigboss@example.com",
            "consent_given_at": "2026-04-24T10:00:00Z",
            "consent_version": "v1",
            "consent_ip": "1.2.3.4",
        },
        files={"file": ("huge.pdf", io.BytesIO(big_pdf), "application/pdf")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_upload_bad_extension_returns_422(client: AsyncClient) -> None:
    """Non-PDF/DOCX uploads must be rejected with 422."""
    response = await client.post(
        "/candidates",
        data={
            "name": "Bad Actor",
            "email": "bad@example.com",
            "consent_given_at": "2026-04-24T10:00:00Z",
            "consent_version": "v1",
            "consent_ip": "1.2.3.4",
        },
        files={"file": ("malware.exe", io.BytesIO(b"MZ\x90"), "application/octet-stream")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_missing_consent_ip_returns_422(client: AsyncClient) -> None:
    """consent_ip is a required form field — omitting it must return 422."""
    response = await client.post(
        "/candidates",
        data={
            "name": "No Consent",
            "email": "noconsent@example.com",
            "consent_given_at": "2026-04-24T10:00:00Z",
            "consent_version": "v1",
            # consent_ip deliberately omitted
        },
        files={"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_bad_timestamp_returns_422(client: AsyncClient) -> None:
    """Garbage datetime for consent_given_at must be rejected with 422."""
    response = await client.post(
        "/candidates",
        data={
            "name": "Bad Time",
            "email": "badtime@example.com",
            "consent_given_at": "not-a-date",
            "consent_version": "v1",
            "consent_ip": "1.2.3.4",
        },
        files={"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_404(client: AsyncClient) -> None:
    """A candidate_id belonging to a different tenant must look like 404, not a data leak."""
    other_tenant_candidate_id = uuid.uuid4()

    # Patch the repo to simulate a candidate that exists but for a different tenant
    # (the repo's get() is tenant-scoped and returns None for cross-tenant queries)
    with patch(
        "app.db.repos.CandidateRepo.get_status",
        new=AsyncMock(return_value=None),
    ):
        response = await client.get(f"/candidates/{other_tenant_candidate_id}")

    assert response.status_code == 404
