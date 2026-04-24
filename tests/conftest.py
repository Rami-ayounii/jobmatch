# tests/conftest.py
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import AuthContext, get_current_user
from app.db.session import AsyncSessionLocal
from app.main import app

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _auth_override() -> AuthContext:
    return AuthContext(
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        clerk_user_id="test_clerk_user",
    )


@pytest.fixture(scope="session")
async def setup_test_tenant() -> None:
    """Seed a tenant + user once per test session. Committed so API sessions see it."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, clerk_org_id) "
                    "VALUES (:id, :name, :org) ON CONFLICT DO NOTHING"
                ),
                {"id": str(TEST_TENANT_ID), "name": "Test Co", "org": "org_test"},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, clerk_user_id, email) "
                    "VALUES (:id, :tid, :uid, :email) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(TEST_USER_ID),
                    "tid": str(TEST_TENANT_ID),
                    "uid": "test_clerk_user",
                    "email": "recruiter@test.com",
                },
            )


@pytest.fixture
async def client(setup_test_tenant: None) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_current_user] = _auth_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def unique_form() -> dict[str, str]:
    """Unique name + email per test to avoid dedup collisions between tests."""
    uid = uuid.uuid4().hex[:8]
    return {
        "name": f"Jane {uid}",
        "email": f"jane-{uid}@example.com",
        "consent_given_at": "2026-04-24T10:00:00Z",
        "consent_version": "v1",
        "consent_ip": "1.2.3.4",
    }
