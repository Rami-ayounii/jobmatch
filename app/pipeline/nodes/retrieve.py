"""Retrieve pipeline node: hybrid pgvector + pg_trgm retrieval → top 50 candidates."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.repos import CandidateRepo
from app.db.session import AsyncSessionLocal
from app.pipeline.state import PipelineState, RetrievedCandidate


async def retrieve_candidates(state: PipelineState) -> PipelineState:
    tenant_id = uuid.UUID(state["tenant_id"])

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            results = await CandidateRepo(db).retrieve_hybrid(
                tenant_id,
                query_embedding=state["job_embedding"],
                query_text=state["job_description"],
                top_k=50,
            )

    retrieved: list[RetrievedCandidate] = [
        RetrievedCandidate(
            candidate_id=str(r.candidate_id),
            resume_text=r.resume_text,
            structured_jsonb=r.structured_jsonb,
            cosine_score=r.cosine_score,
            text_score=r.text_score,
            combined_score=r.combined_score,
        )
        for r in results
    ]

    return {**state, "retrieved_candidates": retrieved}
