"""persist_results pipeline node: write match + invites in a single DB transaction."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.db.repos import AuditRepo, InviteRepo, MatchRepo
from app.db.session import AsyncSessionLocal
from app.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


async def persist_results(state: PipelineState) -> PipelineState:
    tenant_id = uuid.UUID(state["tenant_id"])
    run_id_uuid = uuid.UUID(state["run_id"])

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )

            match_repo = MatchRepo(db)
            invite_repo = InviteRepo(db)
            audit_repo = AuditRepo(db)

            match = await match_repo.get(tenant_id, run_id_uuid)
            if match is None:
                logger.error("persist_results match_not_found run_id=%s", state["run_id"])
                return {**state, "error": "match " + state["run_id"] + " not found"}

            shortlist_data = [
                {
                    "rank": e["rank"],
                    "candidate_id": e["candidate_id"],
                    "score": e["score"],
                    "justification": e["justification"],
                    "citations": e.get("citations", []),
                }
                for e in state.get("shortlist", [])
            ]

            await match_repo.update_status(
                tenant_id,
                match.id,
                status="completed",
                shortlist_jsonb=shortlist_data,
                agent_trace_jsonb={"trace": state.get("agent_trace", [])},
                cost_usd=state.get("total_cost_usd", 0.0),
                completed_at=datetime.now(UTC),
            )

            job_id = uuid.UUID(state["job_id"])

            for draft in state.get("draft_invites", []):
                await invite_repo.create(
                    tenant_id,
                    match_id=match.id,
                    candidate_id=uuid.UUID(draft["candidate_id"]),
                    job_id=job_id,
                    draft_subject=draft["subject"],
                    draft_body=draft["body"],
                )

            await audit_repo.log(
                tenant_id,
                "match_completed",
                entity_type="match",
                entity_id=match.id,
                payload={
                    "run_id": state["run_id"],
                    "shortlist_count": len(shortlist_data),
                    "cost_usd": state.get("total_cost_usd", 0.0),
                },
                cost_usd=state.get("total_cost_usd", 0.0),
            )

    return state
