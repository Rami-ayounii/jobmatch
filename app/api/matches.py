"""Match pipeline endpoints."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.db.repos import AuditRepo, JobRepo, MatchRepo
from app.db.session import get_db
from app.workers.match_worker import run_pipeline

router = APIRouter(prefix="/matches", tags=["matches"])


class MatchRunRequest(BaseModel):
    job_id: uuid.UUID


class MatchRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str


class ShortlistEntryOut(BaseModel):
    rank: int
    candidate_id: uuid.UUID
    score: float
    justification: str
    citations: list[str]


class MatchStatusResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    job_id: uuid.UUID
    created_at: datetime
    completed_at: datetime | None = None
    cost_usd: float | None = None
    shortlist: list[ShortlistEntryOut] | None = None
    error: str | None = None


@router.post("/run", status_code=202, response_model=MatchRunResponse)
async def run_match(
    body: MatchRunRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchRunResponse:
    job = await JobRepo(db).get(ctx.tenant_id, body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    match = await MatchRepo(db).create(
        ctx.tenant_id,
        job_id=body.job_id,
        triggered_by=ctx.user_id,
    )
    await AuditRepo(db).log(
        ctx.tenant_id,
        "match_run_requested",
        actor_id=ctx.user_id,
        entity_type="match",
        entity_id=match.id,
    )

    run_pipeline.delay(
        run_id=str(match.id),
        tenant_id=str(ctx.tenant_id),
        job_id=str(body.job_id),
        user_id=str(ctx.user_id),
    )

    return MatchRunResponse(run_id=match.id, status="pending")


@router.get("/{run_id}", response_model=MatchStatusResponse)
async def get_match(
    run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchStatusResponse:
    match = await MatchRepo(db).get(ctx.tenant_id, run_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    shortlist = None
    if match.shortlist_jsonb and isinstance(match.shortlist_jsonb, list):
        shortlist = [
            ShortlistEntryOut(
                rank=e["rank"],
                candidate_id=uuid.UUID(e["candidate_id"]),
                score=e["score"],
                justification=e["justification"],
                citations=e.get("citations", []),
            )
            for e in match.shortlist_jsonb
        ]

    return MatchStatusResponse(
        run_id=match.id,
        status=match.status,
        job_id=match.job_id,
        created_at=match.created_at,
        completed_at=match.completed_at,
        cost_usd=float(match.cost_usd) if match.cost_usd is not None else None,
        shortlist=shortlist,
        error=match.error,
    )
