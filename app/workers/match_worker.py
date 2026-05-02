"""Match pipeline Celery worker."""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_pipeline(
    run_id: str,
    tenant_id: str,
    job_id: str,
    user_id: str,
) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.db.repos import MatchRepo
    from app.db.session import AsyncSessionLocal
    from app.pipeline.graph import build_graph
    from app.pipeline.state import PipelineState

    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
            )
            await MatchRepo(db).update_status(
                uuid.UUID(tenant_id), uuid.UUID(run_id), status="running"
            )

    initial_state: PipelineState = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "job_id": job_id,
        "user_id": user_id,
        "loop_count": 0,
        "total_cost_usd": 0.0,
        "agent_trace": [],
    }

    graph = build_graph()
    try:
        final_state = await graph.ainvoke(initial_state)
        if final_state.get("error"):
            logger.error("pipeline_error run_id=%s error=%s", run_id, final_state["error"])
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await db.execute(
                        text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
                    )
                    await MatchRepo(db).update_status(
                        uuid.UUID(tenant_id),
                        uuid.UUID(run_id),
                        status="failed",
                        error=final_state["error"],
                    )
    except Exception as exc:
        logger.exception("pipeline_exception run_id=%s", run_id)
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
                )
                await MatchRepo(db).update_status(
                    uuid.UUID(tenant_id),
                    uuid.UUID(run_id),
                    status="failed",
                    error=str(exc)[:500],
                )


@celery_app.task(name="match.run_pipeline", bind=True)
def run_pipeline(
    self: object,
    run_id: str,
    tenant_id: str,
    job_id: str,
    user_id: str,
) -> None:
    asyncio.run(_run_pipeline(run_id, tenant_id, job_id, user_id))
