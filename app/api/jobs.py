"""Job description endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.db.repos import JobRepo
from app.db.session import get_db
from app.services.embeddings import embed_text

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    title: str
    description_text: str


class JobResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    created_at: datetime


@router.post("", status_code=201, response_model=JobResponse)
async def create_job(
    body: JobCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    embedding = await embed_text(body.description_text)
    job = await JobRepo(db).create(
        ctx.tenant_id,
        created_by=ctx.user_id,
        title=body.title,
        description_text=body.description_text,
        embedding=embedding,
    )
    return JobResponse(
        id=job.id,
        title=job.title,
        status=job.status,
        created_at=job.created_at,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    job = await JobRepo(db).get(ctx.tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(
        id=job.id,
        title=job.title,
        status=job.status,
        created_at=job.created_at,
    )
