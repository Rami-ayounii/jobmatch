"""Match pipeline worker — runs the full LangGraph pipeline. Implemented in Phase 4."""
from app.workers.celery_app import celery_app


@celery_app.task(name="match.run_pipeline", bind=True)
def run_pipeline(self: object, run_id: str, job_id: str, tenant_id: str) -> None:
    raise NotImplementedError("Phase 4")
