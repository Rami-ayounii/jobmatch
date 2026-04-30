"""Adversarial: cross-tenant isolation must fail at the DB layer (RLS), not app layer.

Requires a real Postgres instance. Skip automatically when DATABASE_URL points to
a non-existent host or when the pgvector extension is absent.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import encrypt_bytes

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="module")
async def db_engine():
    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not available")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def bypass_session(db_engine):
    """Session that bypasses RLS — simulates the migration role writing seed data."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session


@pytest_asyncio.fixture
async def rls_session(db_engine):
    """Session factory that can SET LOCAL app.tenant_id to activate RLS."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _make(tenant_id: uuid.UUID) -> AsyncSession:
        session = factory()
        await session.begin()
        await session.execute(
            text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)}
        )
        return session

    return _make


# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_tenant(session: AsyncSession, name: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, clerk_org_id) VALUES (:id, :name, :org_id)"
        ),
        {"id": str(tid), "name": name, "org_id": f"org_{tid}"},
    )
    return tid


async def _insert_user(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    uid = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, clerk_user_id, email) "
            "VALUES (:id, :tid, :cuid, :email)"
        ),
        {
            "id": str(uid),
            "tid": str(tenant_id),
            "cuid": f"user_{uid}",
            "email": f"{uid}@example.com",
        },
    )
    return uid


async def _insert_candidate(
    session: AsyncSession, tenant_id: uuid.UUID
) -> uuid.UUID:
    cid = uuid.uuid4()
    encrypted = encrypt_bytes(b'{"name":"Test","email":"t@t.com"}')
    await session.execute(
        text(
            "INSERT INTO candidates "
            "(id, tenant_id, full_name_hash, email_hash, contact_jsonb_encrypted, "
            "resume_text, r2_key, consent_given_at) "
            "VALUES (:id, :tid, :fnh, :eh, :enc, :rt, :r2, :consent)"
        ),
        {
            "id": str(cid),
            "tid": str(tenant_id),
            "fnh": "hash_a",
            "eh": "email_hash_a",
            "enc": encrypted,
            "rt": "resume text",
            "r2": "r2/key/test",
            "consent": datetime.now(UTC),
        },
    )
    return cid


async def _insert_job(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID
) -> uuid.UUID:
    jid = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, created_by, title, description_text) "
            "VALUES (:id, :tid, :by, :title, :desc)"
        ),
        {
            "id": str(jid),
            "tid": str(tenant_id),
            "by": str(created_by),
            "title": "Engineer",
            "desc": "A job description.",
        },
    )
    return jid


# ── tests ──────────────────────────────────────────────────────────────────────


async def test_candidate_cross_tenant_blocked(bypass_session, rls_session):
    """Tenant B's RLS session must not see tenant A's candidates."""
    tenant_a = await _insert_tenant(bypass_session, "Tenant A")
    tenant_b = await _insert_tenant(bypass_session, "Tenant B")
    candidate_a = await _insert_candidate(bypass_session, tenant_a)
    await bypass_session.commit()

    session_b = await rls_session(tenant_b)
    try:
        result = await session_b.execute(
            text("SELECT id FROM candidates WHERE id = :cid"),
            {"cid": str(candidate_a)},
        )
        row = result.fetchone()
        assert row is None, "RLS failed: tenant B can read tenant A's candidate"
    finally:
        await session_b.rollback()
        await session_b.close()


async def test_job_cross_tenant_blocked(bypass_session, rls_session):
    """Tenant B's RLS session must not see tenant A's jobs."""
    tenant_a = await _insert_tenant(bypass_session, "Tenant A2")
    tenant_b = await _insert_tenant(bypass_session, "Tenant B2")
    user_a = await _insert_user(bypass_session, tenant_a)
    job_a = await _insert_job(bypass_session, tenant_a, user_a)
    await bypass_session.commit()

    session_b = await rls_session(tenant_b)
    try:
        result = await session_b.execute(
            text("SELECT id FROM jobs WHERE id = :jid"),
            {"jid": str(job_a)},
        )
        row = result.fetchone()
        assert row is None, "RLS failed: tenant B can read tenant A's job"
    finally:
        await session_b.rollback()
        await session_b.close()


async def test_no_tenant_id_returns_nothing(db_engine):
    """When app.tenant_id is not set, RLS returns zero rows (not an error)."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            result = await session.execute(text("SELECT id FROM candidates LIMIT 10"))
            rows = result.fetchall()
            assert rows == [], f"Expected no rows without tenant_id, got {len(rows)}"


async def test_audit_events_cross_tenant_blocked(bypass_session, rls_session):
    """Tenant B cannot read tenant A's audit events."""
    tenant_a = await _insert_tenant(bypass_session, "Tenant AuditA")
    tenant_b = await _insert_tenant(bypass_session, "Tenant AuditB")
    event_id = uuid.uuid4()
    await bypass_session.execute(
        text(
            "INSERT INTO audit_events (id, tenant_id, event_type) "
            "VALUES (:id, :tid, 'test_event')"
        ),
        {"id": str(event_id), "tid": str(tenant_a)},
    )
    await bypass_session.commit()

    session_b = await rls_session(tenant_b)
    try:
        result = await session_b.execute(
            text("SELECT id FROM audit_events WHERE id = :eid"),
            {"eid": str(event_id)},
        )
        assert result.fetchone() is None, "RLS failed: tenant B can read tenant A's audit event"
    finally:
        await session_b.rollback()
        await session_b.close()
