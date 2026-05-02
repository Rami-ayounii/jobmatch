"""Tenant-scoped repository layer.

All public methods take tenant_id as the first argument.
No raw SQL outside this module — callers never touch db.execute() directly.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Candidate, Invite, Job, Match, Tenant, User


class CandidateRetrievalResult(BaseModel):
    candidate_id: uuid.UUID
    resume_text: str
    structured_jsonb: dict[str, Any]
    cosine_score: float
    text_score: float
    combined_score: float


# ── Tenants ───────────────────────────────────────────────────────────────────


class TenantRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_clerk_org_id(self, clerk_org_id: str) -> Tenant | None:
        result = await self._db.execute(
            select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, name: str, clerk_org_id: str) -> Tenant:
        tenant = Tenant(name=name, clerk_org_id=clerk_org_id)
        self._db.add(tenant)
        await self._db.flush()
        return tenant


# ── Users ─────────────────────────────────────────────────────────────────────


class UserRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_clerk_user_id(
        self, tenant_id: uuid.UUID, clerk_user_id: str
    ) -> User | None:
        result = await self._db.execute(
            select(User).where(
                User.clerk_user_id == clerk_user_id,
                User.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, tenant_id: uuid.UUID, clerk_user_id: str, email: str, role: str = "recruiter"
    ) -> User:
        user = User(
            tenant_id=tenant_id,
            clerk_user_id=clerk_user_id,
            email=email,
            role=role,
        )
        self._db.add(user)
        await self._db.flush()
        return user


# ── Candidates ────────────────────────────────────────────────────────────────


class CandidateRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, tenant_id: uuid.UUID, candidate_id: uuid.UUID) -> Candidate | None:
        result = await self._db.execute(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
                Candidate.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def find_by_hashes(
        self, tenant_id: uuid.UUID, full_name_hash: str, email_hash: str
    ) -> Candidate | None:
        result = await self._db.execute(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id,
                Candidate.full_name_hash == full_name_hash,
                Candidate.email_hash == email_hash,
                Candidate.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        full_name_hash: str,
        email_hash: str,
        contact_jsonb_encrypted: bytes,
        resume_text: str,
        structured_jsonb: dict[str, Any],
        r2_key: str,
        consent_given_at: datetime,
        consent_version: str,
    ) -> Candidate:
        candidate = Candidate(
            tenant_id=tenant_id,
            full_name_hash=full_name_hash,
            email_hash=email_hash,
            contact_jsonb_encrypted=contact_jsonb_encrypted,
            resume_text=resume_text,
            structured_jsonb=structured_jsonb,
            r2_key=r2_key,
            consent_given_at=consent_given_at,
            consent_version=consent_version,
        )
        self._db.add(candidate)
        await self._db.flush()
        return candidate

    async def soft_delete(self, tenant_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            update(Candidate)
            .where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
                Candidate.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
            .returning(Candidate.id)
        )
        return result.scalar_one_or_none() is not None

    async def update_after_parse(
        self,
        tenant_id: uuid.UUID,
        candidate_id: uuid.UUID,
        *,
        resume_text: str,
        structured_jsonb: dict[str, Any],
        embedding: list[float],
        contact_jsonb_encrypted: bytes,
    ) -> None:
        await self._db.execute(
            update(Candidate)
            .where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
            )
            .values(
                resume_text=resume_text,
                structured_jsonb=structured_jsonb,
                embedding=embedding,
                contact_jsonb_encrypted=contact_jsonb_encrypted,
                updated_at=datetime.now(UTC),
            )
        )

    async def get_status(
        self, tenant_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> dict[str, Any] | None:
        result = await self._db.execute(
            select(
                Candidate.id,
                Candidate.embedding,
                Candidate.created_at,
            ).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
                Candidate.deleted_at.is_(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "status": "processing" if row.embedding is None else "ready",
            "created_at": row.created_at,
        }

    async def retrieve_hybrid(
        self,
        tenant_id: uuid.UUID,
        *,
        query_embedding: list[float],
        query_text: str,
        top_k: int = 50,
    ) -> list[CandidateRetrievalResult]:
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        rows = await self._db.execute(
            sa_text("""
                SELECT
                    id,
                    resume_text,
                    structured_jsonb,
                    1 - (embedding <=> :query_vec::vector(1536)) AS cosine_score,
                    COALESCE(word_similarity(:query_text, resume_text), 0.0) AS text_score,
                    0.7 * (1 - (embedding <=> :query_vec::vector(1536))) +
                    0.3 * COALESCE(word_similarity(:query_text, resume_text), 0.0)
                        AS combined_score
                FROM candidates
                WHERE
                    tenant_id = :tenant_id
                    AND deleted_at IS NULL
                    AND embedding IS NOT NULL
                ORDER BY combined_score DESC
                LIMIT :top_k
            """),
            {
                "query_vec": vec_str,
                "query_text": query_text,
                "tenant_id": str(tenant_id),
                "top_k": top_k,
            },
        )
        return [
            CandidateRetrievalResult(
                candidate_id=row.id,
                resume_text=row.resume_text,
                structured_jsonb=row.structured_jsonb,
                cosine_score=float(row.cosine_score),
                text_score=float(row.text_score),
                combined_score=float(row.combined_score),
            )
            for row in rows
        ]


# ── Jobs ──────────────────────────────────────────────────────────────────────


class JobRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
        result = await self._db.execute(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        created_by: uuid.UUID,
        title: str,
        description_text: str,
        structured_jsonb: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> Job:
        job = Job(
            tenant_id=tenant_id,
            created_by=created_by,
            title=title,
            description_text=description_text,
            structured_jsonb=structured_jsonb or {},
            embedding=embedding,
        )
        self._db.add(job)
        await self._db.flush()
        return job


# ── Matches ───────────────────────────────────────────────────────────────────


class MatchRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, tenant_id: uuid.UUID, match_id: uuid.UUID) -> Match | None:
        result = await self._db.execute(
            select(Match).where(Match.tenant_id == tenant_id, Match.id == match_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self, tenant_id: uuid.UUID, *, job_id: uuid.UUID, triggered_by: uuid.UUID
    ) -> Match:
        match = Match(tenant_id=tenant_id, job_id=job_id, triggered_by=triggered_by)
        self._db.add(match)
        await self._db.flush()
        return match

    async def update_status(
        self,
        tenant_id: uuid.UUID,
        match_id: uuid.UUID,
        status: str,
        *,
        shortlist_jsonb: dict[str, Any] | None = None,
        agent_trace_jsonb: dict[str, Any] | None = None,
        cost_usd: float | None = None,
        duration_s: float | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if shortlist_jsonb is not None:
            values["shortlist_jsonb"] = shortlist_jsonb
        if agent_trace_jsonb is not None:
            values["agent_trace_jsonb"] = agent_trace_jsonb
        if cost_usd is not None:
            values["cost_usd"] = cost_usd
        if duration_s is not None:
            values["duration_s"] = duration_s
        if error is not None:
            values["error"] = error
        if completed_at is not None:
            values["completed_at"] = completed_at

        await self._db.execute(
            update(Match)
            .where(Match.tenant_id == tenant_id, Match.id == match_id)
            .values(**values)
        )

    async def anonymize_candidate(
        self, tenant_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> None:
        result = await self._db.execute(
            select(Match.id, Match.shortlist_jsonb).where(
                Match.tenant_id == tenant_id,
                Match.shortlist_jsonb.isnot(None),
            )
        )
        rows = result.all()
        for row in rows:
            shortlist = row.shortlist_jsonb
            if not isinstance(shortlist, list):
                continue
            changed = False
            for entry in shortlist:
                if str(entry.get("candidate_id")) == str(candidate_id):
                    entry["name"] = "[removed]"
                    entry["justification"] = "[removed]"
                    changed = True
            if changed:
                await self._db.execute(
                    update(Match)
                    .where(Match.id == row.id)
                    .values(shortlist_jsonb=shortlist)
                )


# ── Invites ───────────────────────────────────────────────────────────────────


class InviteRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, tenant_id: uuid.UUID, invite_id: uuid.UUID) -> Invite | None:
        result = await self._db.execute(
            select(Invite).where(Invite.tenant_id == tenant_id, Invite.id == invite_id)
        )
        return result.scalar_one_or_none()

    async def list_for_match(self, tenant_id: uuid.UUID, match_id: uuid.UUID) -> list[Invite]:
        result = await self._db.execute(
            select(Invite).where(
                Invite.tenant_id == tenant_id, Invite.match_id == match_id
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        match_id: uuid.UUID,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        draft_subject: str,
        draft_body: str,
    ) -> Invite:
        invite = Invite(
            tenant_id=tenant_id,
            match_id=match_id,
            candidate_id=candidate_id,
            job_id=job_id,
            draft_subject=draft_subject,
            draft_body=draft_body,
        )
        self._db.add(invite)
        await self._db.flush()
        return invite

    async def approve(
        self, tenant_id: uuid.UUID, invite_id: uuid.UUID, approved_by: uuid.UUID
    ) -> bool:
        now = datetime.now(UTC)
        result = await self._db.execute(
            update(Invite)
            .where(
                Invite.tenant_id == tenant_id,
                Invite.id == invite_id,
                Invite.status == "awaiting_approval",
            )
            .values(status="approved", approved_by=approved_by, approved_at=now)
            .returning(Invite.id)
        )
        return result.scalar_one_or_none() is not None

    async def reject(
        self,
        tenant_id: uuid.UUID,
        invite_id: uuid.UUID,
        rejected_reason: str | None = None,
    ) -> bool:
        result = await self._db.execute(
            update(Invite)
            .where(
                Invite.tenant_id == tenant_id,
                Invite.id == invite_id,
                Invite.status == "awaiting_approval",
            )
            .values(status="rejected", rejected_reason=rejected_reason)
            .returning(Invite.id)
        )
        return result.scalar_one_or_none() is not None

    async def list_by_status(
        self, tenant_id: uuid.UUID, status: str | None = None
    ) -> list[Invite]:
        q = select(Invite).where(Invite.tenant_id == tenant_id)
        if status is not None:
            q = q.where(Invite.status == status)
        q = q.order_by(Invite.created_at.desc())
        result = await self._db.execute(q)
        return list(result.scalars().all())

    async def mark_sent(
        self, tenant_id: uuid.UUID, invite_id: uuid.UUID, resend_message_id: str
    ) -> None:
        await self._db.execute(
            update(Invite)
            .where(Invite.tenant_id == tenant_id, Invite.id == invite_id)
            .values(status="sent", resend_message_id=resend_message_id)
        )

    async def mark_failed(
        self, tenant_id: uuid.UUID, invite_id: uuid.UUID, error: str
    ) -> None:
        await self._db.execute(
            update(Invite)
            .where(Invite.tenant_id == tenant_id, Invite.id == invite_id)
            .values(status="failed", rejected_reason=error[:500])
        )


# ── Audit events ──────────────────────────────────────────────────────────────


class AuditRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        *,
        actor_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        cost_usd: float | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_jsonb=payload or {},
            cost_usd=cost_usd,
        )
        self._db.add(event)
        await self._db.flush()
        return event
