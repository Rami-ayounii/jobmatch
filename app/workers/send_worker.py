"""Send worker — post-approval email dispatch via Resend. Implemented in Phase 5.

SAFETY INVARIANT: This worker MUST check status == "approved" AND approved_by IS NOT NULL
before sending any email. This check must never be removed or weakened.
"""
from app.workers.celery_app import celery_app


@celery_app.task(name="send.dispatch_invite", bind=True)
def dispatch_invite(self: object, run_id: str, invite_id: str, tenant_id: str) -> None:
    raise NotImplementedError("Phase 5")
