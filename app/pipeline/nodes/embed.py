"""embed_query pipeline node: loads job from DB, populates embedding + metadata."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.repos import JobRepo
from app.db.session import AsyncSessionLocal
from app.pipeline.state import PipelineState


async def embed_query(state: PipelineState) -> PipelineState:
    """Load job from DB and populate embedding + metadata in state.

    Args:
        state: Current pipeline state containing run_id, tenant_id, job_id

    Returns:
        Updated state with job_title, job_description, and job_embedding populated,
        or error key if job not found.
    """
    tenant_id = uuid.UUID(state["tenant_id"])
    job_id = uuid.UUID(state["job_id"])

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            job = await JobRepo(db).get(tenant_id, job_id)

    if job is None:
        return {**state, "error": f"job {job_id} not found for tenant {tenant_id}"}

    return {
        **state,
        "job_title": job.title,
        "job_description": job.description_text,
        "job_embedding": list(job.embedding) if job.embedding is not None else [],
    }
